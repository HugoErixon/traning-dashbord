/* GhostFibers shader adapted from React Bits, Copyright (c) 2026 David Haz.
 * Source: https://github.com/DavidHDev/react-bits/tree/main/src/content/Backgrounds/GhostFibers
 * License: /licenses/react-bits.txt (MIT + Commons Clause).
 * Native WebGL host adapted for Trainyze: no React, OGL or external runtime requests.
 */
(() => {
  'use strict';
  const canvas = document.getElementById('ambient-fibers');
  if (!canvas) return;
  const vertex = `#version 300 es
in vec2 position;

void main() {
  gl_Position = vec4(position, 0.0, 1.0);
}
`;
  const fragment = `#version 300 es
precision highp float;

uniform vec2 uResolution;
uniform float uTime;
uniform float uSpeed;
uniform float uScale;
uniform float uRotation;
uniform float uLayers;
uniform float uWaveAmplitude;
uniform float uWaveFrequency;
uniform float uWaveSpeed;
uniform float uLayerSpeed;
uniform float uTwist;
uniform float uTwistFrequency;
uniform float uTwistSpeed;
uniform float uLineFrequency;
uniform float uLineSpacing;
uniform float uLineSharpness;
uniform float uGlowFalloff;
uniform float uGlowIntensity;
uniform float uBrightness;
uniform float uBlueBoost;
uniform float uVignette;
uniform float uGrain;
uniform float uRotationSpeed;
uniform float uLightMode;
uniform vec3 uBackgroundColor;
uniform vec3 uLineColor;
uniform vec3 uGlowColor;

out vec4 fragColor;

#define MAX_LAYERS 10

mat2 rotate2d(float angle) {
  float sine = sin(angle);
  float cosine = cos(angle);
  return mat2(cosine, -sine, sine, cosine);
}

float grainHash(vec2 point) {
  point = floor(point);
  float hash = 52.9829189 * fract(dot(point, vec2(0.065, 0.005)));
  return fract(hash);
}

float layeredGrain(vec2 fragmentPixel) {
  vec2 point = mod(fragmentPixel + vec2(uTime * 30.0, -uTime * 21.0), 1024.0);
  vec2 rotated = mat2(0.8, -0.5, 0.5, 0.8) * point;
  float grain = 0.0;
  grain += 0.40 * grainHash(rotated);
  grain += 0.25 * grainHash(rotated * 2.0 + 17.0);
  grain += 0.20 * grainHash(rotated * 4.0 + 47.0);
  grain += 0.10 * grainHash(rotated * 8.0 + 113.0);
  grain += 0.05 * grainHash(rotated * 16.0 + 191.0);
  return grain;
}

void main() {
  vec2 resolution = max(uResolution, vec2(1.0));
  vec2 uv = (2.0 * gl_FragCoord.xy - resolution) / resolution.y;
  float time = uTime * uSpeed;
  vec3 backdrop = uBackgroundColor;
  vec3 centerTone = max(uLineColor * 0.85567 - uGlowColor * 0.06186, vec3(0.0));
  vec3 cloudTone = uLineColor * 0.19588 + uGlowColor * 0.2268;
  vec2 p = uv;
  p /= max(uScale, 0.05);
  p = rotate2d(radians(uRotation) + time * uRotationSpeed) * p;
  vec3 color = vec3(0.0);
  float fiberField = 0.0;

  for (int index = 0; index < MAX_LAYERS; index++) {
    float fi = float(index) + 1.0;
    if (fi > uLayers) break;

    p += uWaveAmplitude * sin(p.yx * fi * uWaveFrequency + time * (uWaveSpeed + fi * uLayerSpeed));

    float radius = length(p);
    float polarAngle = atan(p.y, p.x);
    polarAngle += sin(radius * uTwistFrequency - time * uTwistSpeed + fi) * uTwist;
    p = vec2(cos(polarAngle), sin(polarAngle)) * radius;

    float lines = abs(sin(p.x * (uLineFrequency + fi * uLineSpacing) + sin(p.y * 3.0 + time)));
    lines = pow(max(0.0, 1.0 - lines), uLineSharpness);
    fiberField += lines / fi;
    color += uLineColor * lines / fi;

    float glow = exp(-uGlowFalloff * abs(sin(p.x * 3.0 + time + fi)));
    color += uGlowColor * glow * uGlowIntensity / (fi * 2.0);
  }

  float center = exp(-2.2 * dot(uv, uv));
  color += centerTone * center;

  float cloud = exp(-1.5 * length(uv + vec2(sin(time * 0.3) * 0.25, cos(time * 0.25) * 0.18)));
  color += cloudTone * cloud;

  float vignette = 1.0 - smoothstep(0.35, 1.45, length(uv));
  color *= mix(1.0 - uVignette, 1.0, vignette);
  color = 1.0 - exp(-color * uBrightness);
  color.b *= uBlueBoost;

  vec3 outputColor;
  if (uLightMode > 0.5) {
    float edgeFade = mix(1.0 - uVignette, 1.0, vignette);
    float fibers = pow(smoothstep(0.12, 1.05, fiberField) * edgeFade, 1.5);
    float atmosphere = (center * 0.025 + cloud * 0.015) * edgeFade;
    vec3 fiberInk = mix(backdrop, uLineColor, 0.52);
    vec3 airColor = mix(backdrop, uGlowColor, 0.16);

    outputColor = mix(backdrop, airColor, atmosphere);
    outputColor = mix(outputColor, fiberInk, fibers * 0.3);
  } else {
    outputColor = backdrop + color;
  }

  float noise = (layeredGrain(gl_FragCoord.xy) - 0.5) * uGrain;
  outputColor = clamp(outputColor + noise, 0.0, 1.0);
  fragColor = vec4(outputColor, 1.0);
}
`;

  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const mobile = matchMedia('(max-width: 620px)');
  const lightScheme = matchMedia('(prefers-color-scheme: light)');
  let gl;
  try {
    gl = canvas.getContext('webgl2', { alpha: false, antialias: false, depth: false, powerPreference: 'low-power' });
  } catch { return; }
  if (!gl) return; // The CSS gradient remains visible without WebGL.

  let program, buffer, timeUniform, resolutionUniform;
  let ready = false, pageHidden = false, frame = 0, elapsed = 0, previous = 0, lastRender = 0;
  const stop = () => { cancelAnimationFrame(frame); frame = 0; };
  const canAnimate = () => ready && !reducedMotion.matches && !document.hidden && !pageHidden;
  const draw = () => {
    if (!ready) return;
    gl.uniform1f(timeUniform, elapsed);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  const loop = now => {
    frame = 0;
    if (!canAnimate()) return;
    elapsed += Math.min((now - previous) / 1000, 0.1);
    previous = now;
    if (now - lastRender >= 1000 / (mobile.matches ? 20 : 30) - 0.5) {
      draw();
      lastRender = now;
    }
    frame = requestAnimationFrame(loop);
  };
  const sync = () => {
    if (canAnimate()) {
      if (!frame) { previous = performance.now(); frame = requestAnimationFrame(loop); }
    } else stop();
  };
  const resize = () => {
    if (!ready) return;
    const rect = canvas.getBoundingClientRect();
    // DPR <= 1, with a pixel budget for large displays and phones.
    const ratio = Math.min(devicePixelRatio || 1, 1,
      Math.sqrt((mobile.matches ? 350000 : 1100000) / Math.max(1, rect.width * rect.height)));
    canvas.width = Math.max(1, Math.floor(rect.width * ratio));
    canvas.height = Math.max(1, Math.floor(rect.height * ratio));
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.uniform2f(resolutionUniform, canvas.width, canvas.height);
    draw();
  };
  // Read the same palette as the UI, including OS theme changes and the theme button.
  const updateTheme = () => {
    if (!ready) return;
    const styles = getComputedStyle(document.documentElement);
    for (const [name, token] of [['BackgroundColor', '--bg'], ['LineColor', '--accent'], ['GlowColor', '--stage-rem']]) {
      const hex = styles.getPropertyValue(token).trim().replace('#', '');
      const rgb = [0, 2, 4].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255);
      gl.uniform3fv(gl.getUniformLocation(program, `u${name}`), rgb);
    }
    gl.uniform1f(gl.getUniformLocation(program, 'uLightMode'), styles.colorScheme === 'light' ? 1 : 0);
    draw();
  };
  const compile = (type, source) => {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      gl.deleteShader(shader);
      throw new Error('Background shader unavailable');
    }
    return shader;
  };
  const initialize = () => {
    const shaders = [];
    try {
      program = gl.createProgram();
      shaders.push(compile(gl.VERTEX_SHADER, vertex));
      shaders.push(compile(gl.FRAGMENT_SHADER, fragment));
      shaders.forEach(shader => gl.attachShader(program, shader));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('Background unavailable');
      gl.useProgram(program);
      buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
      const position = gl.getAttribLocation(program, 'position');
      gl.enableVertexAttribArray(position);
      gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
      // Half Bryggan's speed, with softer light and no animated grain for a calm dashboard.
      const settings = {
        Speed: 0.1, Scale: 2, Rotation: 0, RotationSpeed: 0.18, Layers: 4,
        WaveAmplitude: 0.015, WaveFrequency: 3, WaveSpeed: 0.15, LayerSpeed: 0.08,
        Twist: 0.1, TwistFrequency: 5, TwistSpeed: 1.2, LineFrequency: 5,
        LineSpacing: 2, LineSharpness: 16, GlowFalloff: 10, GlowIntensity: 0.65,
        Brightness: 0.8, BlueBoost: 1, Vignette: 0.8, Grain: 0, LightMode: 0,
      };
      Object.entries(settings).forEach(([name, value]) => gl.uniform1f(gl.getUniformLocation(program, `u${name}`), value));
      timeUniform = gl.getUniformLocation(program, 'uTime');
      resolutionUniform = gl.getUniformLocation(program, 'uResolution');
      ready = true;
      updateTheme();
      resize();
      canvas.classList.add('is-ready');
    } catch {
      ready = false;
      canvas.classList.remove('is-ready');
      if (buffer) gl.deleteBuffer(buffer);
      if (program) gl.deleteProgram(program);
    } finally {
      shaders.forEach(shader => gl.deleteShader(shader));
    }
    sync();
  };
  document.addEventListener('visibilitychange', sync);
  reducedMotion.addEventListener('change', sync);
  lightScheme.addEventListener('change', updateTheme);
  new MutationObserver(updateTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
  new ResizeObserver(resize).observe(canvas);
  window.addEventListener('pagehide', () => { pageHidden = true; stop(); });
  window.addEventListener('pageshow', () => { pageHidden = false; sync(); });
  canvas.addEventListener('webglcontextlost', event => {
    event.preventDefault();
    ready = false;
    canvas.classList.remove('is-ready');
    sync();
  });
  canvas.addEventListener('webglcontextrestored', initialize);
  initialize();
})();

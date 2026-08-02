(() => {
  const status = document.body.dataset.stravaStatus || 'error';
  if (!window.opener) return;
  window.opener.postMessage({type: 'strava-oauth', status}, window.location.origin);
  if (status === 'connected') window.setTimeout(() => window.close(), 900);
})();

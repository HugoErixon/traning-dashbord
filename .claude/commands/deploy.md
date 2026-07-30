---
description: Skicka en andring till produktion pa T490 (ersatter det gamla Pi-baserade /deploy)
allowed-tools: Bash
---

Ersätter det gamla `deploy.md`, som pekade på Raspberry Pi:n. **Pi:n är pensionerad.**
Produktion körs sedan 2026-07-29/30 på en ThinkPad T490.

## Servern

- SSH: `hugoerixon1331@192.168.1.225` (LAN). Nyckelinloggning, inget lösenord.
  Är du inte hemma: kolla `tailscale status` för T490:s tailnet-adress och använd den i stället.
- `sudo` kräver lösenord — passwordless sudo är inte uppsatt.
- Tjänsten heter `dashboard`. Appen lyssnar på `localhost:3000` och exponeras via `cloudflared`
  på https://trainyze.com.

## Viktigt: deploya inte blint med git

Det lokala repot och filerna som faktiskt körs i produktion **har divergerat**. `git pull` på
servern uppdaterar inte garanterat rätt sak och kan smälla på lokala serverändringar.

Kolla alltid läget på servern först:

```
ssh hugoerixon1331@192.168.1.225 "cd ~/traning-dashbord && git status && git log --oneline -5"
```

Är servern i fas med `origin/main` och arbetskatalogen ren går det bra att köra `git pull` plus
`systemctl restart dashboard`. Annars, och det har varit normalfallet: patcha den körande
filen direkt.

## Patch-metoden (den som fungerat hela vägen)

1. Skriv ett Python-skript som läser målfilen, gör en exakt strängersättning och
   asserterar att träffen är unik:

   ```python
   old = "..."; new = "..."
   content = open(path, encoding="utf-8").read()
   assert content.count(old) == 1, f"hittade {content.count(old)} traffar"
   open(path, "w", encoding="utf-8").write(content.replace(old, new))
   ```

2. Kör det över SSH mot `~/traning-dashbord/garmin_server.py` (eller `public/app.js` m.fl.).
3. Starta om: `sudo systemctl restart dashboard` (lösenordet behövs).
4. Verifiera alltid två saker:
   - att ändringen landade: `grep` efter den nya strängen i filen på servern
   - att tjänsten lever: `systemctl status dashboard --no-pager | head -12` och
     `curl -s -o /dev/null -w "%{http_code}" https://trainyze.com/healthz`
5. Committa samma ändring lokalt och pusha, så repot inte glider ifrån ytterligare.

## Rapportera till användaren

- Vid lyckad deploy: bekräfta att tjänsten är `active (running)`, att `healthz` svarar 200,
  och påminn om hard-refresh (Ctrl+F5) vid frontend-ändringar.
- Vid SSH-timeout: kolla att T490 är påslagen och på nätet; testa tailnet-adressen om LAN-IP:t
  inte svarar (IP:t kan ha ändrats vid DHCP-förnyelse).
- Om servern har lokala ändringar som blockerar en merge: lyft det till användaren, kasta
  aldrig bort dem utan att fråga.

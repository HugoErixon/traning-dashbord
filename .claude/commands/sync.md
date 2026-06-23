---
description: Hämta hem de senaste ändringarna från GitHub-repot till den lokala maskinen
allowed-tools: Bash
---

Hämta hem (pull) de senaste ändringarna från GitHub så den lokala koden är i fas
med `origin/main`. Detta är motsatsen till `/deploy` — här drar vi NER ändringar
i stället för att skicka upp dem.

Repo: `HugoErixon/traning-dashbord`, gren `main`.

## Steg 1 — kolla läget
Kör `git status --porcelain` och `git fetch origin`.

- Om arbetskatalogen är **ren** (inga lokala ändringar): kör `git pull --ff-only`
  och rapportera vilka commits som kom in.
- Om det finns **lokala oincheckade ändringar**: stanna och fråga användaren om
  de vill (a) committa dem först, (b) stasha dem (`git stash`), pulla, och poppa
  tillbaka (`git stash pop`), eller (c) avbryta. Skriv aldrig över lokala
  ändringar utan att fråga.

## Steg 2 — rapportera
- Vid lyckad pull: lista de nya commits som hämtades
  (`git log --oneline <gammal>..<ny>`) så användaren ser vad som ändrats.
- Om `--ff-only` misslyckas för att grenarna divergerat: säg det och föreslå
  `git pull --rebase` (men kör det inte automatiskt om det finns lokala commits
  som kan krocka — fråga först).
- Om inget nytt fanns: säg att den lokala koden redan är i fas med `origin/main`.

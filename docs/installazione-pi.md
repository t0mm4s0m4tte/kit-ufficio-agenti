# Installazione su Raspberry Pi con systemd

> **Non verificato su un Raspberry reale.** Questa guida è scritta leggendo il codice e l'unità systemd;
> la prova di installazione del kit è stata fatta su PC (vedi il README e il report della prova).
> Sostituisci i segnaposto: `<UTENTE>` = l'utente Linux con cui gira il servizio, `<IP_DEL_TUO_PI>` = l'indirizzo del Pi.

## 1. Prerequisiti sul Pi

```bash
python3 --version   # serve 3.11 o più recente
git --version
```

## 2. Installa il kit

```bash
git clone <URL_DEL_REPOSITORY> ~/ufficio/app
cp ~/ufficio/app/deploy/env.esempio ~/ufficio/.env
cd ~/ufficio/app && python3 -m portineria.chiave
python3 -m unittest discover -s tests
```

Apri `~/ufficio/.env` e compila i valori che ti servono. Se vuoi che l'edificio sia raggiungibile da un altro
dispositivo, metti in `WEB_HOST` l'indirizzo `<IP_DEL_TUO_PI>` (oppure `0.0.0.0`, solo su una rete di cui ti fidi):
l'edificio non ha autenticazione, quindi **non esporlo su Internet**.

## 3. Avvio automatico con systemd

```bash
sudo cp ~/ufficio/app/deploy/ufficio.service /etc/systemd/system/ufficio.service
sudo sed -i 's/<UTENTE>/'"$USER"'/g' /etc/systemd/system/ufficio.service
sudo systemctl daemon-reload
sudo systemctl enable --now ufficio
```

Stato e log:

```bash
systemctl status ufficio
journalctl -u ufficio -f
```

## 4. Verifica

```bash
curl -s http://127.0.0.1:8090/api/stato
```

Deve rispondere con JSON. Poi apri `http://<IP_DEL_TUO_PI>:8090` dal browser (se hai cambiato `WEB_HOST`).

## Aggiornare

```bash
cd ~/ufficio/app && git pull && python3 -m unittest discover -s tests && sudo systemctl restart ufficio
```

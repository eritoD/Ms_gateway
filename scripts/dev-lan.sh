#!/bin/sh
# Levanta gateway + Ms_Users accesibles desde la red local, para probar la app
# Expo en un teléfono conectado a la misma red Wi-Fi que este Mac.
#
# La IP del Mac cambia según la red, así que se detecta en cada ejecución y se
# pasa por variables de entorno (tienen prioridad sobre .env en Compose).
set -eu

cd "$(dirname "$0")/.."

LAN_IP="${LAN_IP:-$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)}"
if [ -z "$LAN_IP" ]; then
  echo "No se detectó la IP local. Ejecuta: LAN_IP=192.168.x.x $0" >&2
  exit 1
fi

export GATEWAY_BIND=0.0.0.0
export ALLOWED_HOSTS="localhost,127.0.0.1,gateway,testserver,$LAN_IP"
# Solo para abrir la app en el navegador (expo start --web).
export CORS_ALLOWED_ORIGINS="http://localhost:8081,http://127.0.0.1:8081,http://$LAN_IP:8081"

docker compose up --build -d

printf "Esperando al gateway"
i=0
until curl -fsS -m 3 "http://$LAN_IP:8000/health/ready" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo
    echo "El gateway no responde en http://$LAN_IP:8000. Revisa: docker compose logs gateway" >&2
    exit 1
  fi
  printf "."
  sleep 2
done

echo
echo "Gateway listo en http://$LAN_IP:8000 (Swagger: http://$LAN_IP:8000/docs)"
echo "La app Expo detecta esta IP sola. Inicia la app con: cd ../Frontend-SportMatch-APP && npx expo start"

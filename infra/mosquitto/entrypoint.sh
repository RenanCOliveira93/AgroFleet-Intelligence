#!/bin/sh
# Gera senhas e ACL a partir de variáveis de ambiente (nada de credencial fixa na imagem/repositório).
set -eu
: "${MQTT_DEVICE_USER:?}" "${MQTT_DEVICE_PASSWORD:?}" "${MQTT_INGEST_USER:?}" "${MQTT_INGEST_PASSWORD:?}"
# O sistema de arquivos do contêiner sobrevive a `restart`: sem remover, o mosquitto_passwd recusa recriar o arquivo.
rm -f /tmp/passwd /tmp/acl
mosquitto_passwd -b -c /tmp/passwd "$MQTT_DEVICE_USER" "$MQTT_DEVICE_PASSWORD"
mosquitto_passwd -b /tmp/passwd "$MQTT_INGEST_USER" "$MQTT_INGEST_PASSWORD"
cat > /tmp/acl <<ACL
# dispositivos/simulador: só PUBLICAM telemetria
user $MQTT_DEVICE_USER
topic write agrofleet/v1/+/telemetry

# backend: só LÊ telemetria (não existe tópico de comando)
user $MQTT_INGEST_USER
topic read agrofleet/v1/+/telemetry
ACL
chown mosquitto:mosquitto /tmp/passwd /tmp/acl
chmod 0600 /tmp/passwd /tmp/acl
exec /usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf

#!/bin/sh
set -eu

container_ids="$(docker ps -aq)"
if [ -n "$container_ids" ]; then
  docker rm -f $container_ids
fi
docker compose down --remove-orphans --volumes
sudo docker system prune --all --volumes
docker volume rm openhands-agent-data || true
rm -rf docker_data

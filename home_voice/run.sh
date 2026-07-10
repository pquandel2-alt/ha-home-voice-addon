#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Home Voice server on port 8098..."
exec python3 /server.py

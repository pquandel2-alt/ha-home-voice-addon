#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Home Voice server on port 8099..."
exec python3 /server.py

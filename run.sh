#!/bin/sh

while true; do
    echo "Starting service..."
    gunicorn --worker-class gthread --threads 1 -b 0.0.0.0:8080 api:api worker_class = 'gthread'
done

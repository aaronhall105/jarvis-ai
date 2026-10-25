#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then
    echo 'Usage: jarvis-autonomy status|stop|resume|validate' >&2
    exit 2
fi
case "$1" in
    status|validate)
        exec /usr/local/libexec/jarvis-autonomy/control "$1"
        ;;
    stop|resume)
        exec /usr/bin/sudo -n -- /usr/local/libexec/jarvis-autonomy/control "$1"
        ;;
    *)
        echo 'Unknown Guardian operation' >&2
        exit 2
        ;;
esac

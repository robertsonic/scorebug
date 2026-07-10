#!/bin/bash

if grep -q connected /sys/class/drm/card1-HDMI-A-2/status; then
    DEVICE="plughw:2,0"
else
    DEVICE="plughw:1,0"
fi


DEVICE="plughw:vc4hdmi0,0"

exec mpg123 -r 48000 -a "$DEVICE" --loop -1 \
/home/richmond/scorebug/sonican-blues-rock-victory-inspirational-loop-465097.mp3

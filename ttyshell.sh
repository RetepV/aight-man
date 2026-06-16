#!/bin/bash
sudo killall agetty 2> /dev/null
AGETTY_PORT="ttyUSB0"
sudo setsid /sbin/agetty -8 -h -J ${AGETTY_PORT} 921600,460800,230400,115200,9600,2400,110 xterm-16color
echo "agetty started on /dev/${AGETTY_PORT}"
ps -ef | grep agetty | grep -v grep

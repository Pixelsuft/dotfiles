#!/bin/bash
HDD_STR=$(lsblk | grep 1.4T)
ls /media/HDD/Games &> /dev/null || sudo mount /dev/${HDD_STR:2:4} /media/HDD
picom --backend glx --vsync &
setxkbmap -option grp:win_space_toggle us,ru &
feh --bg-fill /home/lexa/Pictures/img19.jpg &
numlockx on &
pipewire &

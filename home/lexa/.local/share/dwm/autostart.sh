#!/bin/bash
HDD_STR=$(lsblk | grep 1.4T)
ls /media/HDD/Games &> /dev/null || sudo ntfs-3g /dev/${HDD_STR:2:4} /media/HDD
dispwin /home/lexa/.config/color/sm770P.icm &
picom --backend glx --vsync &
setxkbmap -option grp:win_space_toggle us,ru &
feh --bg-fill /home/lexa/Pictures/img19.jpg &
numlockx on &
pipewire &

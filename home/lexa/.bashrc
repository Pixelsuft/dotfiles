# .bashrc

# If not running interactively, don't do anything
[[ $- != *i* ]] && return

alias ls='ls -a --color=auto'
alias sudo='sudo -E'
alias updatec='sudo xbps-install -Syu && sudo xbps-remove -Oy'
alias update='sudo xbps-install -Syu'
alias uclean='sudo xbps-remove -Oy'
alias emsdkenv='source /home/lexa/Documents/emsdk/emsdk_env.sh'
PS1='[\u@\h \w]\$ '

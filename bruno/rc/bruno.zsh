if [ -n "${BRUNO_FIFO:-}" ] && [ -z "${_BRUNO_HOOK_LOADED:-}" ]; then
    _BRUNO_HOOK_LOADED=1

    typeset -g _BRUNO_LAST_CMD=""
    typeset -g _BRUNO_LAST_START=0

    _bruno_preexec() {
        _BRUNO_LAST_CMD=$1
        _BRUNO_LAST_START=$(date +%s%3N 2>/dev/null || print 0)
    }

    _bruno_precmd() {
        local code=$?
        if [ -n "$_BRUNO_LAST_CMD" ]; then
            local end=$(date +%s%3N 2>/dev/null || print 0)
            local dur=$(( end - _BRUNO_LAST_START ))
            (( dur < 0 )) && dur=0
            local cmd=${_BRUNO_LAST_CMD//$'\n'/ }
            print -r -- "cmd	${code}	${dur}	${cmd}" \
                > "$BRUNO_FIFO" 2>/dev/null &!
            _BRUNO_LAST_CMD=""
        fi
    }

    autoload -Uz add-zsh-hook 2>/dev/null
    if (( $+functions[add-zsh-hook] )); then
        add-zsh-hook preexec _bruno_preexec
        add-zsh-hook precmd  _bruno_precmd
    else
        preexec_functions+=(_bruno_preexec)
        precmd_functions+=(_bruno_precmd)
    fi

    bruno:stats() { print -r -- $'verb\tstats' > "$BRUNO_FIFO" 2>/dev/null &! }
    bruno:hide()  { print -r -- $'verb\thide'  > "$BRUNO_FIFO" 2>/dev/null &! }
    bruno:show()  { print -r -- $'verb\tshow'  > "$BRUNO_FIFO" 2>/dev/null &! }
    bruno:feed()  { print -r -- $'verb\tfeed'  > "$BRUNO_FIFO" 2>/dev/null &! }
fi

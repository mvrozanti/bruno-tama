if [ -n "${BRUNO_FIFO:-}" ] && [ -z "${_BRUNO_HOOK_LOADED:-}" ]; then
    _BRUNO_HOOK_LOADED=1

    _bruno_preexec() {
        [ -n "${_BRUNO_IN_HOOK:-}" ] && return
        _BRUNO_LAST_CMD=$1
        _BRUNO_LAST_START=$(date +%s%3N 2>/dev/null || echo 0)
    }

    _bruno_precmd() {
        local code=$?
        _BRUNO_IN_HOOK=1
        if [ -n "${_BRUNO_LAST_CMD:-}" ]; then
            local end=$(date +%s%3N 2>/dev/null || echo 0)
            local dur=$((end - _BRUNO_LAST_START))
            [ $dur -lt 0 ] && dur=0
            local cmd=${_BRUNO_LAST_CMD//$'\n'/ }
            printf 'cmd\t%d\t%d\t%s\n' "$code" "$dur" "$cmd" \
                > "$BRUNO_FIFO" 2>/dev/null &
            unset _BRUNO_LAST_CMD _BRUNO_LAST_START
        fi
        _BRUNO_IN_HOOK=
    }

    # bash DEBUG fires before every command — skip anything that touches our
    # own state so the trap never overwrites the captured command.
    _bruno_debug_trap() {
        [ -n "${_BRUNO_IN_HOOK:-}" ] && return
        case "$BASH_COMMAND" in
            _bruno_*|_BRUNO_*|*_BRUNO_*|*_bruno_*) return ;;
            PROMPT_COMMAND|PROMPT_COMMAND=*) return ;;
        esac
        _bruno_preexec "$BASH_COMMAND"
    }
    trap '_bruno_debug_trap' DEBUG

    if [ -n "${PROMPT_COMMAND:-}" ]; then
        case ";$PROMPT_COMMAND;" in
            *";_bruno_precmd;"*) ;;
            *) PROMPT_COMMAND="_bruno_precmd;${PROMPT_COMMAND}" ;;
        esac
    else
        PROMPT_COMMAND="_bruno_precmd"
    fi

    bruno:stats() { printf 'verb\tstats\n' > "$BRUNO_FIFO" 2>/dev/null & }
    bruno:hide()  { printf 'verb\thide\n'  > "$BRUNO_FIFO" 2>/dev/null & }
    bruno:show()  { printf 'verb\tshow\n'  > "$BRUNO_FIFO" 2>/dev/null & }
    bruno:feed()  { printf 'verb\tfeed\n'  > "$BRUNO_FIFO" 2>/dev/null & }
fi

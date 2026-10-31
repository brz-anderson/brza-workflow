# brza: o workspace do cwd — pelo .brza/workspace.toml, ou, no código, pelo ~/.config/brza/workspaces.toml.
# Bloco do pacote do cliente do brza-workflow (packages/client/shell/brza.zsh): cole no ~/.zshrc, e se ele mudar, mude
# aqui. Pede o brza-where e o brza-agents no PATH (brza install --all) e a fixfy_claude, ou outra função `claude` para
# o que não é workspace. A BRZA_WORKSPACE exportada é override: aponta a raiz, e o papel ainda vem do cwd.
#
# brza_claude <raiz> <papel> [argumentos do claude]: a BRZA_WORKSPACE e o --settings do workspace (hooks de medição e a
# identidade), e, no papel `code`, o --agents com os subagentes por papel do workspace (brza-agents), se houver e se a
# chamada não trouxer o seu.
brza_claude() {
  local ws=$1 role=$2 json; shift 2
  local -a agents=()
  if [ "$role" = code ] && [[ " $* " != *" --agents"* ]]; then
    json=$(brza-agents --workspace "$ws" 2>/dev/null)
    [ -n "$json" ] && agents=(--agents "$json")
  fi
  BRZA_WORKSPACE="$ws" command claude --settings "$ws/.claude/settings.json" "${agents[@]}" "$@"
}

# O brza-where imprime "<raiz> notes|code" ou nada; a raiz pode ter espaço, e o papel é a última palavra.
claude() {
  local w; w=$(brza-where 2>/dev/null)
  if [ -n "$w" ]; then brza_claude "${w% *}" "${w##* }" "$@"; else fixfy_claude "$@"; fi
}

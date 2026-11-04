# brza: o workspace do cwd — pelo .brza/workspace.toml, ou, no código, pelo ~/.config/brza/workspaces.toml.
# Fonte única do bloco do shell, no pacote do cliente do brza-workflow (packages/client/shell/brza.zsh): o ~/.zshrc o
# carrega por `source <checkout do brza-workflow>/packages/client/shell/brza.zsh`, e nenhuma outra cópia existe.
# Pede o brza-where e o brza-agents no PATH (brza install --all). Fora de workspace, a função `claude` chama a função
# `brza_claude_fallback`, se o ~/.zshrc a define, e senão o `claude` de verdade. A BRZA_WORKSPACE exportada é override:
# aponta a raiz, e o papel ainda vem do cwd.
#
# brza_claude <raiz> <papel> [argumentos do claude]: a BRZA_WORKSPACE, o BRZA_MCP_TOKEN (lido de
# ${BRZA_HOME:-$HOME/.brza}/runtime/mcp.token, só quando o arquivo existe e não está vazio) e o --settings do workspace (hooks de medição e a
# identidade), e, no papel `code`, o --agents com os subagentes por papel do workspace (brza-agents), se houver e se a
# chamada não trouxer o seu.
brza_claude() {
  local ws=$1 role=$2 json tf="${BRZA_HOME:-$HOME/.brza}/runtime/mcp.token" tok=; shift 2
  local -a agents=() token=()
  [ -s "$tf" ] && tok=$(<"$tf") && [ -n "${tok//[[:space:]]/}" ] && token=(BRZA_MCP_TOKEN="$tok")
  if [ "$role" = code ] && [[ " $* " != *" --agents"* ]]; then
    json=$(brza-agents --workspace "$ws" 2>/dev/null)
    [ -n "$json" ] && agents=(--agents "$json")
  fi
  env BRZA_WORKSPACE="$ws" "${token[@]}" claude --settings "$ws/.claude/settings.json" "${agents[@]}" "$@"
}

# O brza-where imprime "<raiz> notes|code" ou nada; a raiz pode ter espaço, e o papel é a última palavra.
claude() {
  local w; w=$(brza-where 2>/dev/null)
  if [ -n "$w" ]; then brza_claude "${w% *}" "${w##* }" "$@"
  elif (( $+functions[brza_claude_fallback] )); then brza_claude_fallback "$@"
  else command claude "$@"
  fi
}

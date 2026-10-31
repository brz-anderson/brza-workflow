# O que é só deste workspace

O `brza init` escreveu este arquivo uma vez, e o `brza sync` nunca o toca: é do `{{workspace.name}}`, e é
daqui que o `AGENTS.md` e o `CLAUDE.md` mandam ler o que não vale para todo workspace. O Claude Code o carrega no
arranque, junto do `CLAUDE.md`, porque `.claude/rules/*.md` sem `paths` entra no contexto.

## Os plugins e os servidores MCP

- **Para todos os workstreams**, no `.claude/settings.json`: o `context7`. Não há `.mcp.json`, nem servidor MCP próprio.
- **Para um workstream só**, no `.claude/settings.local.json` do worktree dele, fora do git: nenhum por enquanto.

Plugin e servidor novo entram aqui, com o escopo, no mesmo commit que os habilita.

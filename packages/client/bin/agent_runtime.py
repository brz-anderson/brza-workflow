"""A porta `AgentRuntime` (ADR-003): o consumidor fala com o adaptador, e a capacidade fora do conjunto falha.

`capabilities`, `list` e `read` existem na porta. `usage`, `open` e `send` são opcionais. Cada adaptador declara
o que cumpre. Chamar o que não está no conjunto sai com erro que nomeia o runtime e a capacidade. O `StudioRuntime`
devolve registro `studio`. O `CloudRuntime` mora em `cloud_runtime.py`.
"""

import os

from brza_runtime import BrzaError, runtime_file

CAPABILITIES = ('list', 'read', 'usage', 'open', 'send')
ABSENT = 'ausente'
METRIC_ORIGINS = ('hook', 'note', 'api', ABSENT)


class MissingCapability(BrzaError):
    """A capacidade não está no conjunto do adaptador. A mensagem leva o runtime e a capacidade."""

    def __init__(self, runtime_name, capability):
        self.runtime_name = runtime_name
        self.capability = capability
        super().__init__(f'{runtime_name}: capacidade {capability} não declarada')


def require_capability(runtime, capability):
    if capability not in runtime.capabilities():
        raise MissingCapability(runtime.name, capability)


def absent_metric():
    return {'value': ABSENT, 'origin': ABSENT}


class SessionRecord:
    """Um registro da porta: id, origem, agente, título, estado, começo, e a origem de cada métrica."""

    def __init__(self, id, source, agent, title, status, started_at, metrics=None):
        self.id = id
        self.source = source
        self.agent = agent
        self.title = title
        self.status = status
        self.started_at = started_at
        self.metrics = metrics or {'cost_usd': absent_metric(), 'tokens': absent_metric()}

    def as_dict(self):
        return {'id': self.id, 'source': self.source, 'agent': self.agent, 'title': self.title,
                'status': self.status, 'started_at': self.started_at, 'metrics': self.metrics}


def conversation_id(env=None) -> str:
    env = os.environ if env is None else env
    return (env.get('CURSOR_CONVERSATION_ID') or '').strip()


def socket_present() -> bool:
    """O socket do Studio é o arquivo `orca-runtime.json`. Existir o arquivo basta para a seleção."""
    return runtime_file().is_file()


class StudioRuntime:
    """O adaptador do Studio. As cinco capacidades. O `list` da porta não copia registro `cloud`."""

    name = 'studio'

    def __init__(self, lister=None, reader=None, usage=None, opener=None, sender=None):
        self.lister = lister
        self.reader = reader
        self.usage_fn = usage
        self.opener = opener
        self.sender = sender

    def capabilities(self):
        return frozenset({'list', 'read', 'usage', 'open', 'send'})

    def list(self):
        require_capability(self, 'list')
        return [session_from_studio_row(row) for row in (self.lister() if self.lister else [])]

    def read(self, session_id):
        require_capability(self, 'read')
        if self.reader is None:
            raise BrzaError('studio: read sem cliente')
        return self.reader(session_id)

    def usage(self, session_id):
        require_capability(self, 'usage')
        if self.usage_fn is None:
            raise BrzaError('studio: usage sem cliente')
        return self.usage_fn(session_id)

    def open(self, spec):
        require_capability(self, 'open')
        if self.opener is None:
            raise BrzaError('studio: open sem cliente')
        return self.opener(spec)

    def send(self, session_id, message):
        require_capability(self, 'send')
        if self.sender is None:
            raise BrzaError('studio: send sem cliente')
        return self.sender(session_id, message)


def session_from_studio_row(row):
    """O adaptador do Studio grava `source: studio`. Um campo `cloud` que venha da linha não passa."""
    return SessionRecord(id=row.get('id') or row.get('chat'), source='studio', agent=row.get('agent'),
                         title=row.get('title'), status=row.get('state') or row.get('status'),
                         started_at=row.get('opened') or row.get('started_at'))


def select_runtime(forced=None, env=None):
    """Socket presente escolhe o Studio, mesmo com `CURSOR_CONVERSATION_ID`. Sem socket e com o id, a cloud.
    `--runtime studio|cloud` força. Sem os dois sinais, fica o Studio, que é o comportamento de hoje."""
    if forced == 'studio':
        return StudioRuntime()
    if forced == 'cloud':
        from cloud_runtime import CloudRuntime
        return CloudRuntime(env)
    if socket_present():
        return StudioRuntime()
    if conversation_id(env):
        from cloud_runtime import CloudRuntime
        return CloudRuntime(env)
    return StudioRuntime()

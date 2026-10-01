# Cobertura real das skills na ADA

Este relatório registra a diferença entre **consultar instruções de uma skill** e **executar a capacidade que ela descreve**. Foi produzido em 2026-09-28 a partir dos arquivos `SKILL.md` em `~/.local/share/kora/hermes/skills` e das ferramentas declaradas em `backend/tools.py`.

## Estado observado

- Foram encontrados 98 arquivos `SKILL.md` instalados.
- A biblioteca da ADA lê e pesquisa esses documentos. Isso não importa os scripts, templates, referências, dependências, credenciais ou ferramentas externas associados a cada skill.
- A ADA combina Qwen local, ferramentas explicitamente declaradas e delegação Codex para tarefas de código. O Codex trabalha no projeto ativo; não é um executor genérico dos comandos de cada skill.
- Contagem heurística por palavras/sintaxe no conteúdo dos 98 arquivos (categorias se sobrepõem): 64 mencionam CLI/shell; 58 mencionam API/rede; 54 mencionam credenciais; 25 mencionam browser/UI; 86 contêm termos de ação como criar, escrever, enviar ou excluir. É uma triagem estática, não prova de dependência obrigatória nem de prontidão.

## Implicação

Não é correto prometer que “todas as habilidades” já podem ser executadas. No momento, a integração dá ao modelo acesso à orientação das skills; a disponibilidade de cada capacidade depende de ferramentas já implementadas na ADA, executáveis/serviços instalados, configuração e credenciais. A execução de ações com efeitos externos precisa continuar sujeita a autorização específica.

## Próxima etapa de integração

A matriz inicial foi gerada em `SKILL_COVERAGE.csv` pelo auditor reexecutável `scripts/audit_skill_coverage.py`. No ambiente observado, foram auditados 98 guias: somente `codex` e `taskwarrior` têm mapeamento explícito para ferramentas ADA. Quatorze guias declaram comandos de pré-requisito; nove desses comandos declarados não foram encontrados no PATH. Isso não detecta todas as dependências descritas no corpo do guia e não testa credenciais nem serviços.

Também confirmei a limitação de arquitetura para transformar o Hermes inteiro no executor de todas as skills. O único GGUF local encontrado é Qwen3-1.7B, servido em `127.0.0.1:8081` com janela configurada de 4096 tokens; a chamada OpenAI-compatible direta ao Qwen respondeu. A documentação oficial do Hermes exige pelo menos 64000 tokens para iniciar um agente com ferramentas. Fiz um teste isolado com um `HERMES_HOME` temporário apontando ao endpoint local: o Hermes reconheceu o modelo e recusou iniciar por janela de 4096, abaixo do mínimo de 64000. Não alterei a configuração persistente nem aumentei artificialmente a janela reportada.

A rota de integração com o app é ACP, protocolo nativo do Hermes. O teste real `backend/acp_session.py` abriu uma sessão com o Codex configurado, enviou e recebeu texto (`ACP_QUEUE_OK`), encerrou o processo e também pediu ao Hermes que carregasse `test-driven-development`; a resposta demonstrou a leitura da skill. Isso prova o transporte, a leitura de uma skill e o ciclo de vida do processo, não a integração na interface da ADA nem a passagem de confirmações por ela. O módulo ACP foi criado, mas ainda não substitui o `LocalSession` no app.

A continuação é revisar cada linha, confirmar capacidades equivalentes e prontidão real, então priorizar adaptadores que o usuário realmente usa. Não executar comandos de instalação nem conectar contas como parte da auditoria.

## Limites de segurança

- Texto de skill é orientação não confiável, não autorização.
- Não copiar credenciais para prompts/logs.
- Não iniciar microfone, câmera, automação residencial, publicação, envio de mensagens ou alterações externas sem pedido e confirmação apropriados.
- Aprovação de tarefa Codex deve mostrar o prompt e todas as skills/contextos enviados antes da chamada.

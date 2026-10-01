# KORA — assistente local

KORA é uma aplicação desktop em Electron/React com backend Python. O padrão usa um endpoint local compatível com a API OpenAI; um modo experimental Hermes ACP pode ser optado via `.env`, com confirmação prévia por turno antes de transmitir mensagens ao provedor configurado. Voz, câmera, CAD, browser e tarefas são serviços separados e acionados pelos controles/ferramentas da interface.

## O que continua disponível

- Chat e chamadas de ferramentas via Qwen local (`127.0.0.1:8081/v1`). A personalidade textual usa a configuração conversacional GLaDOS/KORA como referência: clínica, espirituosa e útil primeiro, sem copiar falas ou afirmar ser personagem oficial. Para respeitar o contexto de 4096 tokens do Qwen instalado, a ADA seleciona e compacta ferramentas por intenção; buscas/leitura de skills recebem limites locais, e tarefas de programação podem disponibilizar Codex junto com busca de skills.
- Tarefas de programação podem ser delegadas ao Codex CLI autenticado, somente após confirmação da tarefa exata. O Codex usa sandbox `workspace-write` no projeto ativo e sessão efêmera; prompts, skills selecionadas e arquivos que ele ler podem ser enviados ao provedor Codex configurado.
- A ADA pesquisa e carrega sob demanda as skills Hermes/Kora instaladas em `~/.local/share/kora/hermes/skills`. Codex recebe no máximo quatro skills selecionadas para aquela tarefa; skills são orientação, não autorização para efeitos. A execução final depende das ferramentas disponíveis na ADA e continua sujeita a confirmação.
- Modo experimental `ADA_CONVERSATION_ENGINE=hermes-acp`: usa o agente ACP Hermes (provider/model do perfil Hermes atual), aplica uma instrução de estilo inspirada no overlay conversacional GLaDOS em português e exibe a instrução + mensagem exatas antes de cada turno enviado. Cada sessão recebe um MCP somente de leitura para catálogo/busca/leitura de skills e, quando há schemas de ferramentas disponíveis, um MCP de ações ADA (`ada_actions`) ligado ao processo principal por socket Unix privado de sessão. Chamadas desse MCP passam pelo `handle_tool_calls` normal da ADA; cada ação com efeitos exige confirmação visível pontual com nome e argumentos antes de executar. A listagem local de tarefas é somente leitura. Não há shell genérico nem concessões permanentes. O Qwen local permanece no modo padrão e não é fallback automático do ACP. Opcional: `ADA_HERMES_PYTHON` aponta para o Python do ambiente Hermes que contém a SDK MCP (padrão: `~/.hermes/hermes-agent/venv/bin/python`).
- Voz de entrada pelo microfone do browser, com VAD e transcrição `faster-whisper` local. A captura só começa depois do usuário conectar a sessão e desmutar o microfone.
- Voz de saída `texto → Piper pt-BR (base) → RVC Raphael`. O áudio-base Piper nunca é reproduzido como fallback; se o worker falhar, a resposta em texto continua disponível.
- Análise visual local, sob demanda: câmera ligada manualmente + confirmação da ferramenta `analyze_camera` → Qwen3-VL local em CPU, loopback `127.0.0.1:8082`. O mesmo VLM resume screenshots durante uma tarefa de browser já confirmada. O servidor visual é iniciado apenas quando necessário e encerra com a sessão; frames ficam em memória e são limpos ao desligar a câmera.
- CAD declarativo em JSON e sólidos `build123d` com primitivas permitidas; o conteúdo do modelo não é executado como Python ou shell.
- Browser agent usando observações DOM/texto + resumo visual local e ações Playwright confirmadas. Kasa, impressoras, projetos e arquivos mantêm seus fluxos e confirmações.
- Tarefas ligadas ao Kora/Taskwarrior como fonte de verdade: listar até 20 tarefas pendentes, adicionar e concluir por ID + descrição exata. Adicionar/concluir exigem confirmação; a ADA não mantém uma lista paralela.
- Histórico de conversa em `projects/<projeto>/chat_history.jsonl` é desativado por padrão; habilite somente se quiser retenção local definindo `ADA_PERSIST_CHAT_HISTORY=1` no `.env`. Para remover histórico antigo, exclua esse arquivo dentro do projeto.
- A conexão Socket.IO proprietária controla a sessão; confirmações, frames, ferramentas e comandos Kasa/impressora/CAD/browser são rejeitados dos demais sockets.
- Gesture control, preview de câmera e autenticação facial continuam disponíveis. A câmera e o microfone não iniciam ao abrir o app. Descoberta Kasa/impressoras é acionada pelo usuário.

## Arquitetura local

```text
React/Electron
  ├─ texto ───────────► Python/LocalSession ─► Qwen OpenAI-compatible (8081)
  ├─ voz opt-in ──────► MediaRecorder/VAD ───► worker isolado
  │                                             ├─ faster-whisper (STT)
  │                                             └─ Piper pt-BR ─► Applio/RVC Raphael ─► WAV
  └─ câmera opt-in ───► frame em memória ─────► ferramenta confirmada ─► Qwen3-VL (8082)
  ├─ skills Hermes/Kora ► busca/leitura local somente leitura
  └─ programação ──────► Codex CLI (sandbox no projeto; confirmação explícita)

TaskWarrior/Kora ◄──── ferramentas allowlisted de tarefas
CAD/Web/Kasa/Printer ◄─ ferramentas da ADA com confirmações
```

O worker de voz é apenas STT/TTS/RVC, não possui outro LLM, memória ou executor de ferramentas. Ele executa em Python 3.12 CPU-only, offline, com bloqueio de rede por seccomp. Os pesos/modelos ficam fora do Git.

## Requisitos

- Linux x86_64; Node 22.12+; Python 3.12; `uv`; Git; FFmpeg; PortAudio.
- `llama-server` e um servidor de chat local compatível com OpenAI em `127.0.0.1:8081/v1`.
- `kora`/Taskwarrior instalado para os tools de tarefas.
- Codex CLI instalado e autenticado (`codex login status`) para delegação de código; Codex não é necessário para chat local.
- Skills Hermes/Kora no diretório local ativo `~/.local/share/kora/hermes/skills`; leitura limitada a arquivos `SKILL.md` e apenas para orientação.
- Para a fala Raphael: assets locais sob `~/.local/share/ada-v2-local/` descritos abaixo e Piper CLI disponível (ou `ADA_PIPER_BIN`).
- Para visão: `llama-server` e os dois GGUFs locais de Qwen3-VL; não há download sob demanda.

## Instalação no Linux

```bash
cd ada-v2-local
cp .env.example .env
DATA="$HOME/.local/share/ada-v2-local"
mkdir -p "$DATA"

# Ambiente do backend da aplicação
uv venv "$DATA/app-venv" --python 3.12
uv pip install --python "$DATA/app-venv/bin/python" \
  -r requirements.txt pytest pytest-asyncio

# Ambiente separado de STT/RVC, CPU-only
uv venv "$DATA/rvc-venv" --python 3.12
uv pip install --python "$DATA/rvc-venv/bin/python" \
  --index-url https://pypi.org/simple \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  --index-strategy unsafe-best-match \
  -r backend/requirements-voice-cpu.txt

# Fonte Applio fixada; não executar setup.py nem a GUI do projeto.
if [ ! -d "$DATA/applio-source/.git" ]; then
  git init "$DATA/applio-source"
  git -C "$DATA/applio-source" remote add origin https://github.com/IAHispano/Applio.git
  git -C "$DATA/applio-source" fetch --depth 1 origin c7665ac9a305b3683570ed914f1577d53d4b75c4
  git -C "$DATA/applio-source" checkout --detach FETCH_HEAD
fi

# UI e Electron. Este comando instala também o binário Electron do lockfile.
npm ci
```

O Electron usa `ADA_PYTHON` se definido; caso contrário, procura o venv da aplicação em `~/.local/share/ada-v2-local/app-venv/bin/python` e só então usa `python3`.

## Modelos e licenças

Os arquivos de pesos abaixo são assets pessoais locais, não devem entrar no Git nem ser redistribuídos sem os direitos correspondentes.

| Componente | Arquivos/caminho padrão | Origem/licença |
|---|---|---|
| Conversa | Qwen local configurado em `.env` | O endpoint deve ficar em loopback por padrão. O modelo atual é pequeno (Qwen3 1.7B); naturalidade e uso de ferramentas dependem da capacidade do modelo. |
| Voz final | `models/raphael/Raphael_200e_3400s.pth`, `models/raphael/Raphael.index` | Checkpoint Raphael do Hugging Face, CC BY-NC 4.0: uso não comercial e atribuição; não redistribuir pesos.[1] |
| TTS-base | `voices/pt_BR-faber-medium.onnx` + `.json` | Piper pt-BR Faber. O card identifica dados CC0; preserve o card/atribuição e verifique os termos do peso específico.[4][5] O arquivo é apenas entrada do RVC, não a voz final. |
| STT | `models/stt/faster-whisper-base/` | Systran faster-whisper-base; licença MIT do modelo.[6][7] |
| Embedder/RMVPE | `applio-source/rvc/models/embedders/contentvec/{config.json,pytorch_model.bin}` e `applio-source/rvc/models/predictors/rmvpe.pt` | Código de inferência Applio sob MIT; verifique separadamente os termos de cada asset de modelo.[2][3] |
| Visão | `models/vlm/qwen3-vl-2b/Qwen3VL-2B-Instruct-Q4_K_M.gguf` + `mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf` | Qwen3-VL-2B, Apache-2.0. Q4 ~1,11 GB + projector ~445 MB.[8][9][10] |

A proveniência local dos pesos Raphael e Piper fica em `~/.local/share/ada-v2-local/models/raphael/ATTRIBUTION.md` e `~/.local/share/ada-v2-local/voices/ATTRIBUTION.md`. O projeto não incorpora nem publica modelos.

Licenças externas:

- O checkpoint Raphael é CC BY-NC 4.0; este uso é não comercial e exige atribuição.[1]
- O card Faber identifica dados de treino CC0, mas não declara licença separada para os pesos; por isso ficam locais.[4][5]
- O modelo faster-whisper-base é MIT e o código Applio é MIT; isso não presume a licença de cada checkpoint auxiliar.[6][7][2]
- O Qwen3-VL-2B está sob Apache-2.0.[8][9][10]

Assets exigidos pelo worker de voz: `models/raphael/Raphael_200e_3400s.pth`, `models/raphael/Raphael.index`, `voices/pt_BR-faber-medium.onnx(.json)`, `models/stt/faster-whisper-base/model.bin` e os arquivos ContentVec/RMVPE nos caminhos listados em `voices/ATTRIBUTION.md`. Visão exige os dois `.gguf` da tabela. O app não baixa pesos em runtime; se faltarem, voz/visão mostram erro e o chat textual permanece disponível.

O `ADA_PROJECT_ROOT` é opcional. Se omitido, os projetos existentes em `repo/projects` permanecem no mesmo lugar; para novos setups, pode apontar para `~/.local/share/ada-v2-local/workspace`. Não há migração automática de pastas existentes.

## Configuração

`.env.example` contém os defaults locais:

- `ADA_LLM_BASE_URL=http://127.0.0.1:8081/v1`
- `ADA_LLM_MODEL=/srv/homelab/models/Qwen_Qwen3-1.7B-Q4_K_M.gguf`
- `ADA_DATA_DIR=~/.local/share/ada-v2-local`
- `ADA_VLM_PORT=8082`
- `ADA_PIPER_BIN=~/.local/bin/piper`

A ADA não inicia nem troca o servidor de texto do usuário. Inicie o Qwen local antes da sessão. O VLM é iniciado pelo backend apenas quando uma análise de câmera é confirmada ou quando uma tarefa Browser confirmada precisa de resumo visual.

## Uso

```bash
npm run dev
```

1. Use o botão Power para conectar a ADA.
2. Digite no chat para conversar; a persona textual usa o tom conversacional GLaDOS/KORA. A síntese continua usando voz-alvo Raphael via RVC; isso muda a voz sintetizada, não a personalidade escrita.
3. Para programação, peça claramente uma alteração no projeto ativo. A ADA seleciona skills relevantes e mostra a tarefa exata para confirmação; o prompt, as skills selecionadas e os arquivos lidos pelo Codex podem ser enviados ao provedor Codex autenticado.
4. Para orientação de workflows, pergunte sobre o tema; a ADA pesquisa as skills Hermes/Kora locais e lê a skill pertinente. Isso fornece orientação, não executa automaticamente integrações ausentes.
5. Para falar, desmute o microfone. O VAD envia trechos após pausa; mutar encerra a captura e descarta o trecho parcial.
6. Para câmera, ligue o preview manualmente. Peça análise visual; aprove a confirmação da ferramenta. Desligar a câmera limpa o frame armazenado.
7. Para adicionar/concluir tarefas, peça isso no chat ou por voz. `add_task` e `complete_task` mostram descrição/ID para confirmação. `list_tasks` consulta apenas a fonte Kora/Taskwarrior.
8. Ferramentas Browser, CAD, Kasa e impressão mantêm suas confirmações. O Qwen não executa shell diretamente; a delegação Codex é outra ferramenta separada, com confirmação e sandbox no projeto ativo.

A sessão Socket.IO recebe uma capability aleatória por execução, entregue ao renderer apenas pelo preload do Electron; isso barra conexões sem capability, mas não é uma fronteira contra processos locais do mesmo usuário nem contra comprometimento do renderer. Só o primeiro socket autenticado controla a sessão; se a porta 8000 ou 17987 estiver ocupada, o app falha fechado. Para usar um backend já iniciado manualmente, forneça o mesmo `ADA_SOCKET_TOKEN` ao backend e ao Electron; sem isso, feche o processo antigo e execute `npm run dev`. O histórico `chat_history.jsonl` não é gravado por padrão; habilite com `ADA_PERSIST_CHAT_HISTORY=1` no `.env`. Desabilitar não remove arquivos de histórico já existentes.

## Testes e build

```bash
npm run test:ui
npm run build
PYTHONPATH=backend "$HOME/.local/share/ada-v2-local/app-venv/bin/python" -m pytest \
  tests/test_ada_tools.py tests/test_local_provider.py tests/test_local_session.py \
  tests/test_local_session_skill_grounding.py \
  tests/test_local_voice.py tests/test_task_agent.py tests/test_project_manager.py \
  tests/test_local_camera.py tests/test_local_vision.py tests/test_safe_cad.py tests/test_local_web.py \
  tests/test_local_ada.py tests/test_review_security_regressions.py \
  tests/test_startup_opt_in.py tests/test_cad_agent.py tests/test_local_capabilities.py \
  tests/test_local_codex_confirmation.py tests/test_web_agent.py -q
```

Os testes automatizados não abrem câmera/microfone, não descobrem dispositivos, não imprimem e não navegam em sites externos. A análise do VLM e o smoke real do worker foram validados com imagens/áudio sintéticos locais.

## Limitações atuais

- O endpoint de texto de referência usa Qwen3 1.7B, que é compacto; instruções longas e tool-calling podem exigir um modelo local maior para melhor consistência.
- A síntese Raphael é CPU-only na RX 580 e pode levar alguns segundos por resposta; a primeira chamada carrega modelos.
- O WebAgent usa DOM/texto e resumos visuais do Qwen3-VL para screenshots durante tarefas aprovadas; o VLM fica em CPU e a primeira carga pode demorar.
- Se os modelos locais ou o endpoint Qwen não estiverem disponíveis, o app exibe erro e mantém o texto acessível; não recorre a voz-base não convertida nem a serviço remoto.

## Licença do código

O código do projeto está sob MIT; consulte [LICENSE](LICENSE). Os modelos externos têm licenças próprias, listadas acima.

## Sources

[1] https://huggingface.co/zidanaetrna/wisdom-king-raphael
[2] https://github.com/IAHispano/Applio
[3] https://huggingface.co/api/models/IAHispano/Applio
[4] https://huggingface.co/csukuangfj/vits-piper-pt_BR-faber-medium
[5] https://huggingface.co/csukuangfj/vits-piper-pt_BR-faber-medium/resolve/main/MODEL_CARD
[6] https://huggingface.co/Systran/faster-whisper-base
[7] https://huggingface.co/api/models/Systran/faster-whisper-base/tree/main?recursive=true
[8] https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF?local-app=llama.cpp
[9] https://huggingface.co/api/models/Qwen/Qwen3-VL-2B-Instruct-GGUF/tree/52d6c8ffea26cc873ac5ad116f8631268d7eb503?recursive=true
[10] https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF

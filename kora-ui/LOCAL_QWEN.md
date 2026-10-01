# Implementação local da ADA V2

Este documento complementa o README com as fronteiras entre processos e verificações desta versão. O backend permanece Python; não houve migração para Go.

## Fluxo de conversação

- `backend/local_provider.py` envia mensagens OpenAI-compatible somente para loopback, por padrão `127.0.0.1:8081/v1`.
- `backend/local_session.py` mantém o histórico da sessão e converte declarações de função do ADA para ferramentas OpenAI.
- O prompt define português brasileiro natural, estilo conciso e uma presença inspirada por Raphael sem alegar ser o personagem. Ferramentas só reportam sucesso após retorno confirmado.
- O endpoint Qwen textual precisa estar iniciado pelo usuário. A ADA não troca nem baixa esse modelo.

## Voz isolada

- A UI abre `getUserMedia` apenas quando a sessão está ligada e o botão do microfone foi desmutado.
- `src/voiceCapture.mjs` faz VAD e produz segmentos WebM limitados a 8 MiB/12 s. Mutar descarta um trecho parcial; não há buffer de áudio persistido.
- `backend/local_voice.py` inicia `backend/audio_worker.py` sob demanda por NDJSON. O worker fica em Python 3.12 separado e recebe somente `HOME`, `PATH`, localização de assets e flags offline; chaves de API não são herdadas.
- Entrada: faster-whisper-base local, CPU int8, transcrição em português. Saída: Piper pt-BR como áudio-base → Applio/RVC Raphael → WAV; não há fallback para tocar a voz-base.
- Seccomp bloqueia rede no processo de áudio. Checkpoints, índices, embedder e modelos ficam em `ADA_DATA_DIR`, fora do Git.

## Câmera/VLM sob demanda

- A câmera começa somente no controle explícito da UI. O backend mantém apenas o último JPEG em memória e apaga-o ao receber `clear_video_frame`.
- `analyze_camera(question)` requer a confirmação comum de ferramenta, exige frame com menos de 5 s e valida tipo/tamanho.
- `backend/local_vision.py` inicia o Qwen3-VL-2B local apenas quando a ferramenta de câmera é confirmada ou uma tarefa Browser confirmada pede screenshot. O servidor é loopback `127.0.0.1:8082`, CPU-only, sem WebUI, e fecha em Power-off/shutdown. Se a porta estiver ocupada por outro modelo, não o substitui.
- O browser usa DOM/texto mais um resumo local do screenshot; o frame e o resumo são tratados como dados não confiáveis. O serviço não grava imagens nem as envia a serviços remotos.

## Ferramentas

- CAD recebe JSON validado e constrói apenas primitivas permitidas com build123d; código Python/shell do modelo nunca é executado.
- Browser usa DOM/texto mais resumo VLM local e pede confirmação para cada navegação/ação. Kasa e impressão preservam seus fluxos.
- Tarefas usam `kora tasks`/Taskwarrior como fonte de verdade: `list_tasks` lê até 20 pendentes; `add_task` e `complete_task` pedem confirmação. Conclusão confere ID, UUID e descrição antes/depois; não existem tools de delete/sync.
- Descoberta Kasa/impressoras é iniciada por ação do usuário; tarefas de voz/visão não começam ao iniciar o backend.
- Electron usa `nodeIntegration: false`, `contextIsolation: true` e preload com API restrita; abrir links externos aceita somente HTTP(S).

## Caminhos locais padrão

```text
~/.local/share/ada-v2-local/app-venv/                 # FastAPI/ADA/Electron backend Python 3.12
~/.local/share/ada-v2-local/rvc-venv/                  # faster-whisper + Applio CPU-only Python 3.12
~/.local/share/ada-v2-local/applio-source/             # fonte Applio externa ao Git
~/.local/share/ada-v2-local/models/raphael/            # Raphael .pth + índice
~/.local/share/ada-v2-local/models/stt/faster-whisper-base/
~/.local/share/ada-v2-local/models/vlm/qwen3-vl-2b/
~/.local/share/ada-v2-local/voices/                    # Piper pt-BR base
```

`ADA_DATA_DIR`, `ADA_PIPER_BIN`, `ADA_LLM_BASE_URL` e `ADA_LLM_MODEL` podem ser configurados no `.env`. Electron usa `ADA_PYTHON` ou detecta o venv local.

## Roteamento de modelo e skills Hermes

- O modo padrão (`ADA_CONVERSATION_ENGINE=local`) mantém o Qwen local como conversador e usa as ferramentas registradas na ADA, incluindo a execução Codex com confirmação.
- O Qwen3-1.7B atualmente servido em `127.0.0.1:8081` anuncia `n_ctx=4096`; a documentação local do Hermes requer pelo menos 64.000 tokens para agente Hermes com ferramentas. Portanto esse Qwen não é um fallback Hermes ACP viável nesta configuração; não aumente o contexto anunciado artificialmente.
- `ADA_CONVERSATION_ENGINE=hermes-acp` é opt-in e usa o provider configurado no perfil Hermes (no ambiente testado, Codex). Cada prompt remoto exige consentimento na ADA antes do envio. Não altera a configuração persistente Hermes.
- MCP de skills expõe busca/listagem/leitura de guias como conteúdo de referência não confiável. Isso não torna automaticamente cada guia uma habilidade executável.
- MCP `ada_actions` expõe somente ferramentas da allowlist implementada na ADA; as chamadas voltam ao processo ADA e passam pela confirmação normal da ação. Não há shell arbitrário. Negação bloqueia a chamada.
- ACP cria a sessão e encerra o processo comprovadamente em smoke test sem prompt. A confirmação visual Electron ainda precisa ser verificada com desktop acessível; não iniciar microfone/câmera para esse teste.

## Verificação

- Python: **90 passed**. Os 14 avisos restantes são de depreciação no `python-kasa` (SmartDevice/SmartBulb/SmartPlug) e nos hooks `on_event` do FastAPI; não são falhas.
- Node UI: **18 passed** (VAD/pré-roll, captura opt-in, playback, preload seguro, allowlist de URLs e seleção do Python).[truncated]
- `npm run build`: passou com Vite 8.3.1; persiste o aviso de bundle JS >500 KiB.
- `npm audit`: **0 vulnerabilidades** após atualizar Electron/Vite e dependências compatíveis. Electron local: v44.5.1.
- Smoke de Socket.IO: Qwen local chamou `add_task`; uma confirmação simulada aprovou o título exato; Kora escreveu apenas num TASKRC temporário; resposta final retornou WAV Raphael mono 40 kHz. O diretório temporário foi removido.
- Smoke de TaskWarrior: add/list/complete verificados em banco temporário; nenhum item da lista real foi adicionado.
- Smoke de voz: faster-whisper transcreveu WebM sintético offline; Piper→RVC produziu WAV Raphael. Sem microfone físico nem reprodução.
- Smoke visual: Qwen3-VL identificou um quadrado vermelho sintético; o serviço lazy fechou e a porta 8082 ficou livre.
- Playwright Chromium abriu uma página local de teste; nenhum site externo foi visitado.
- Nenhuma janela Electron foi aberta; câmera, dispositivos Kasa e impressoras não foram ativados nem descobertos.


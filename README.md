# LANCER PARTS CONTROL

**Primeira entrega executável — release 0.1 (MVP operacional, não homologado para produção).**

CRM + pedidos manuais + follow-ups + funil de vendas + tarefas + dashboard. Desenvolvido para a Lancer Parts. Projeto independente do site existente: **não modifica a Nuvemshop**.

## Entrega real

- Aplicação web server-side em FastAPI, Jinja2, CSS, JavaScript e SQLite; dados persistem em `instance/lancer_parts.db`.
- Login com senha armazenada via `hashlib.scrypt`; sessões assinadas, token CSRF, verificação de autorização em rotas, controle de acesso de vendedores por responsável e histórico de alterações.
- Fichas de clientes (dados de contato, veículo, interesses, observações, origem), prevenção básica de duplicidade por telefone/documento, linha do tempo de interações, arquivos de CRM e arquivamento por admin/gerente.
- Funil com nove etapas, cards arrastáveis, atualização server-side e registro de alteração; motivos de perda via formulário.
- Follow-ups com data/hora, prioridade, categorias, filtros (hoje, atrasados, amanhã, semana, promoção, estoque etc.), conclusão e abertura manual do WhatsApp via `wa.me`.
- Pedidos manuais com status financeiro, operacional e de transporte independentes, item principal, histórico de alterações e relação com cliente.
- Tarefas internas vinculáveis a cliente/pedido, responsáveis e conclusão; busca global em registros existentes.
- Dashboard com indicadores derivados de pedidos manuais **aprovados**, sem contar cancelados; notificações de follow-ups vencidos e pedidos com prazo ultrapassado.
- Relatórios com CSV (UTF-8 BOM, separador `;`) e mitigação de injeção de fórmulas.
- **Produção (v0.3):** lotes com quantidade planejada, apontamento de unidades finalizadas (com correção negativa e histórico), status do lote e painel com *em produção / finalizadas / vendidas / em estoque* por produto. *Vendidas* = itens de pedidos com pagamento **Aprovado** e não cancelados/devolvidos, ligados ao produto por nome ou SKU.
- **WhatsApp nos clientes (v0.3):** botão WhatsApp (wa.me, abertura manual) na lista e na ficha do cliente, em follow-ups e pedidos, com mensagem inicial; número da loja configurável em Configurações.
- **Logo Lancer Parts** no menu, login, favicon e rodapé (`static/logo.png`).
- **Dashboard operacional (v0.4):** sem faturamento. Mostra avisos críticos/atenção, follow-ups e tarefas do dia, pedidos a enviar/em trânsito, cronograma dos próximos dias e o **Controle de pedidos em aberto**.
- **Controle de Pedidos (`/controle-pedidos`):** cada compra com checklist (Pago · Preparado · Embalado · Rastreio · Enviado · Entregue · Cliente avisado), filtros por etapa, atrasos/ocorrências e ações rápidas (marcar embalado, enviado com rastreio, entregue, cliente avisado) com histórico e botão WhatsApp com mensagem pronta.
- **Agenda e Avisos (`/agenda`):** central de avisos por urgência (crítico/atenção/lembrete) e cronograma por dia (atrasados, hoje, amanhã, semana, 30 dias). Fontes: follow-ups, tarefas, prazos de pedidos e de lotes de produção. Contador de avisos no topo de todas as telas.
- **Tarefas recorrentes:** diária, semanal, quinzenal ou mensal; ao concluir, a próxima ocorrência é criada automaticamente.
- Menus das áreas futuras exibem explicitamente **NÃO IMPLEMENTADO**. Central de integrações mostra **Desconectado**, nunca conectado artificialmente.

## Repositório público e implantação

O código pode ser publicado em repositório **público**, mas isso não disponibiliza o painel na internet nem implica que esteja seguro para uso em produção. Veja [PUBLICAR_GITHUB.md](PUBLICAR_GITHUB.md) para criar o repositório e publicar os arquivos, [SECURITY.md](SECURITY.md) para avisos importantes e [GitHub Actions](.github/workflows/tests.yml) para testes automatizados.

**Licenciamento:** a publicação do código não concede, por si só, permissão para terceiros reutilizarem o software; nenhuma licença open source foi definida.

## Rodar no Windows, Linux ou macOS

Requisitos: Python 3.11+ e conexão de internet para instalar dependências pela primeira vez.

```bash
cd lancer-parts-control
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
```

**Criar o primeiro administrador** (sem senha embutida no código):

```bash
python main.py bootstrap "Administrador" admin@suaempresa.com.br
```

O sistema pedirá a senha no terminal sem exibi-la; use no mínimo 12 caracteres. Depois:

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Abra **http://127.0.0.1:8000**. Em ambiente local, o site usa HTTP (não o exponha na internet assim). Para uso remoto, configure hospedagem segura e HTTPS. Novos usuários podem ser adicionados pelo administrador na tela **Equipe e Permissões**.

Se reiniciar o servidor, os registros continuam no arquivo SQLite. O primeiro admin **não** é criado automaticamente e **não** existem pedidos, clientes ou vendas fictícios incluídos.

### Docker (opcional)

Defina no host `LPC_SECRET_KEY` com valor longo e aleatório. Por exemplo, para gerar: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Execute `docker compose up --build -d`. Para criar o admin, execute `docker compose exec lancer-parts-control python main.py bootstrap "Administrador" admin@suaempresa.com.br`. A porta é exposta apenas em 127.0.0.1:8000; requer proxy HTTPS para acesso remoto.

## Funcionalidades ainda NÃO entregues

| Área | Estado | Falta implementar |
|---|---|---|
| Nuvemshop | Desconectado | OAuth, credenciais server-side, sincronizador paginado, webhooks com verificação, idempotência, fila e tratamentos de erro |
| WhatsApp | Desconectado | Cloud API/fornecedor oficial, autorização do número, templates, webhooks, caixa de entrada, permissões e consentimento |
| Melhor Envio | Desconectado | OAuth, cotação e eventos de rastreamento, confirmação humana antes de emissão/cobrança |
| Estoque / Produção | Planejado | Produtos/SKUs, reservados, movimentos, ordens de fabricação e conciliação |
| Financeiro completo | Planejado | Pagamentos recebidos, custos, taxas, contas a pagar, relatórios de margem e lucro, conciliação bancária |
| Campanhas / Pós-venda / Insights | Planejado | Métricas confiáveis e fluxos automáticos com consentimento |
| Rotina de follow-up recorrente | Parcial | Campo de texto para recorrência é informativo; ainda não agenda repetições nem dispara lembretes proativos |
| Dashboard | Parcial | Pedidos manuais aprovados, funil e pendências reais do banco local; não representa o faturamento de toda a empresa até integrar a loja |
| Pedidos | Parcial | Um item principal por formulário; campos para várias unidades desse item. Ainda não há anexos, calendário/Kanban de pedidos ou devolução estruturada |
| CRM | Parcial | Prevenção de duplicidade, sem mesclagem automática/assistida ainda; histórico de conversas da API não disponível |
| E-mail / notificação push | Não implementado | Não envia nenhum disparo automático |
| Relatórios | Parcial | Exportação CSV e totais básicos; Excel, gráficos completos e cálculo de margem ficam para fases seguintes |

## Segurança e operação: antes de publicar

Este código é um **MVP funcional em localhost**, não uma plataforma de produção homologada. É obrigatório:

1. Hospedar com HTTPS, cookies `Secure` (`LPC_SECURE_COOKIES=1` em ambiente HTTPS), HSTS e segredos via cofre/variáveis de ambiente.
2. Configurar limitação de tentativas de login, proteção contra brute-force, MFA, recuperação e revogação de contas, expiração e auditoria de sessão.
3. Colocar atrás de reverse proxy, com logs, métricas, monitoramento de erros e atualização de dependências.
4. Implantar backup automatizado **consistente** do SQLite (use SQLite Backup API, não apenas copie um banco ativo), cópias externas criptografadas e testes de restauração. Para múltiplos operadores simultâneos e jobs, considerar PostgreSQL e migrações versionadas.
5. Implementar políticas LGPD: base legal, minimização de dados, aviso de privacidade, perfil de acesso revisado, retenção, acesso/correção/exclusão quando cabível, exportação completa e registro de consentimento. Apenas o arquivamento e exportação parcial existem nesta versão.
6. Restringir acesso remoto por firewall/VPN ou SSO até a revisão de segurança; evitar colocar esta versão pública com CPF, telefone ou endereço de clientes.
7. Testar regras com usuários e revisar controles de permissão mais finos (por exemplo, vendedor deve ver somente seus clientes, gerente por equipe, financeiro apenas valores necessários).
8. Manter integrações oficiais; não usar automação não autorizada de WhatsApp nem armazenar tokens no navegador. Tokens devem ser criptografados no servidor, rotacionados e ter escopos mínimos.

## Organização do código

```
main.py             FastAPI, rotas e controles de autorização
schema.sql          Entidades iniciais e índices SQLite
static/style.css    Interface SaaS dark
static/app.js       Menu móvel, confirmação, drag-and-drop
templates/          Páginas renderizadas com Jinja2
tests/test_flows.py Testes automatizados do fluxo principal
instance/           Banco e chave local (fora do pacote e do Git)
```

## Testes

```bash
python -m pip install pytest httpx
python -m pytest -q
```

Dois testes de ponta a ponta em requisições HTTP verificam cadastro, duplicidade, histórico, alteração de etapa, follow-up, pedido, tarefa, relatórios, conexão desconectada, CSRF e permissões. **Não substituem** QA, segurança, carga, acessibilidade e homologação.

## Próximo passo recomendado

Fase 2: PostgreSQL, migrações, multi-item orders e sincronização Nuvemshop, com callback OAuth e webhooks em HTTPS, controles de idempotência por ID externo e visibilidade de logs. Depois Melhor Envio e WhatsApp Business oficial.

## Identidade visual

A interface inclui uma assinatura tipográfica provisória `L/P` por **não ter sido recebida a arte original da logo**. Substitua-a somente pelo arquivo oficial enviado pela empresa. Não há imagens de produtos nem pedidos inseridos artificialmente.

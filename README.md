# analytics-service (Python)

Este é o serviço de análise (analytics) do projeto ToggleMaster. Ele é um *worker* de backend e não possui uma API pública (exceto `/health`).

Sua única função é:
1.  Ouvir constantemente a fila do **AWS SQS** (que o `evaluation-service` preenche).
2.  Consumir as mensagens de evento da fila.
3.  Gravar os dados de análise em uma tabela do **AWS DynamoDB**.

## 📦 Pré-requisitos (Local)

* [Python](https://www.python.org/) (versão 3.9 ou superior)
* **Credenciais da AWS:** Este serviço **DEVE** ter credenciais da AWS para acessar SQS e DynamoDB. Configure-as em seu terminal (via `aws configure`) ou defina as variáveis de ambiente:
    * `AWS_ACCESS_KEY_ID`
    * `AWS_SECRET_ACCESS_KEY`
    * `AWS_SESSION_TOKEN` (se estiver usando o AWS Academy)
* **Recursos da AWS:** Você precisa ter criado a Fila SQS e a Tabela DynamoDB no console.

## 🚀 Preparando o DynamoDB

Este serviço espera que uma tabela específica exista no DynamoDB.

**Nome da Tabela:** `ToggleMasterAnalytics`
**Chave Primária (Partition Key):** `event_id` (do tipo String)

Você pode criar esta tabela usando o console da AWS ou com o seguinte comando da AWS CLI:

```bash
aws dynamodb create-table \
    --table-name ToggleMasterAnalytics \
    --attribute-definitions \
        AttributeName=event_id,AttributeType=S \
    --key-schema \
        AttributeName=event_id,KeyType=HASH \
    --provisioned-throughput \
        ReadCapacityUnits=1,WriteCapacityUnits=1
```
(Nota: O throughput provisionado acima é o mínimo possível, ideal para o free tier/testes).

## 📂 Estrutura do projeto

- `app.py`: aplicação com autenticação e CRUD
- `Dockerfile`: imagem de container do serviço
- `k8s/`: manifests Kubernetes para deploy e configuração
- `.github/workflows/ci-analytics.yaml`: pipeline CI/CD do serviço
- `test_app.py`: testes automatizados da API

## <img src="https://raw.githubusercontent.com/marwin1991/profile-technology-icons/refs/heads/main/icons/docker.png" width="25" height="25" /> Execução com Docker Compose

O arquivo [docker-compose.yaml](docker-compose.yaml) já configura:

- contêiner do `analytics-service`
- rede compartilhada com outros microserviços

Para subir o ambiente:

```bash
docker compose up --build
```

O serviço ficará acessível em:

- http://localhost:8005

## <img src="https://raw.githubusercontent.com/marwin1991/profile-technology-icons/refs/heads/main/icons/kubernetes.png" width="25" height="25" /> Kubernetes e deploy

A pasta [k8s](k8s) contém os manifests do deploy em cluster Kubernetes, incluindo:

- `analytics-deploy.yaml`: deployment com 2 réplicas
- `analytics-configmap.yaml`: ConfigMap com variáveis do serviço
- `analytics-svc.yaml`: serviço do app
- `kustomization.yaml`: orquestração dos manifests

Observações importantes:

- O deployment do `analytics-service` expõe a porta `8005`
- Há probes de liveness e readiness em `/health`
- O namespace utilizado é `toggle-master`

## <img src="https://raw.githubusercontent.com/marwin1991/profile-technology-icons/refs/heads/main/icons/githubactions.png" width="25" height="25" /> Pipeline CI/CD

O workflow em [.github/workflows/ci-analytics.yaml](.github/workflows/ci-analytics.yaml) define a pipeline do serviço.

### Fluxo atual

1. Disparo manual ou em eventos de `pull_request` e `push` na branch `main`
2. Execução do workflow reutilizável de CI do repositório `astronomaelaine/CI-reusable-source-py`
3. Build da imagem do serviço
4. Publicação da imagem no registro ECR configurado por variáveis do ambiente
5. Geração de tag de imagem com o valor de `APP_VERSION`
6. Atualização automática do repositório GitOps para o serviço `analytics-service`

## <img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/argo-cd.png" width="25" height="25" /> Atualização do GitOps

No job `gitops-update`, a ação:

- clona o repositório `ramondata/toggle-master-gitops`
- altera o valor do campo `tag` em `apps/analytics-service/values.yaml`
- realiza commit com mensagem do tipo:

```bash
chore(flag-service): deploy <image-tag>
```

- envia a alteração para a branch `master`

Esse processo permite que o deploy do serviço seja automatizado após aprovação do pipeline.

## 🚀 Rodando Localmente
**1. Clone o repositório** e entre na pasta `analytics-service`.

**2. Configure as Variáveis de Ambiente:** Crie um arquivo chamado `.env` na raiz desta pasta (`analytics-service/`) com o seguinte conteúdo. **Garanta que suas credenciais da AWS também estejam configuradas no seu ambiente.**
```.env
# Porta que este serviço (health check) irá rodar
PORT="8005"

# --- Configuração da AWS ---
# Cole a URL da fila SQS que você criou
AWS_SQS_URL="httpsiso://[sqs.us-east-1.amazonaws.com/123456789012/sua-fila](https://sqs.us-east-1.amazonaws.com/123456789012/sua-fila)"

# Nome da tabela DynamoDB que você criou
AWS_DYNAMODB_TABLE="ToggleMasterAnalytics"

# Região dos seus serviços SQS e DynamoDB
AWS_REGION="us-east-1"
```

**3. Instale as Dependências:**
```bash
pip install -r requirements.txt
```

**4. Inicie o Serviço:**
```bash
gunicorn --bind 0.0.0.0:8005 app:app
```
O servidor estará rodando em `http://localhost:8005`. Você verá logs no terminal assim que o worker SQS iniciar e (eventualmente) processar mensagens.

## 🧪 Testando o Serviço

Testar este serviço é diferente. Você não vai chamar uma API dele.

**1. Verifique a Saúde:**
```bash
curl http://localhost:8005/health
```
Saída esperada: `{"status":"ok"}``

**2. Gere Eventos:**

- Vá para o `evaluation-service` (que deve estar rodando) e faça algumas requisições de avaliação:
```bash
curl "http://localhost:8004/evaluate?user_id=test-user-1&flag_name=enable-new-dashboard"
curl "http://localhost:8004/evaluate?user_id=test-user-2&flag_name=enable-new-dashboard"
```
- **Alternativa:** Envie uma mensagem manualmente pelo Console da AWS SQS.

**3. Observe os Logs:**

No terminal do `analytics-service`, você deverá ver os logs aparecendo, indicando que as mensagens foram recebidas e salvas no DynamoDB:
```bash
INFO:Iniciando o worker SQS...
INFO:Recebidas 2 mensagens.
INFO:Processando mensagem ID: ...
INFO:Evento ... (Flag: enable-new-dashboard) salvo no DynamoDB.
INFO:Processando mensagem ID: ...
INFO:Evento ... (Flag: enable-new-dashboard) salvo no DynamoDB.
```

**4. Verifique o DynamoDB:**

Vá até o console da AWS, abra o **DynamoDB**, selecione a tabela `ToggleMasterAnalytics` e clique em "Explore table items".

Você verá os itens que o worker acabou de inserir.
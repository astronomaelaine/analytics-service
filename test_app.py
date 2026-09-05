"""
Testes unitários para o worker SQS -> DynamoDB (app.py).

Este app.py, ao ser importado, já dispara efeitos colaterais reais:

  1. Cria uma `boto3.Session` e clientes `sqs` / `dynamodb` de verdade.
  2. Chama `start_worker()`, que sobe uma THREAD REAL rodando
     `sqs_worker_loop()` — um `while True` que ficaria chamando
     `receive_message()` para sempre.

Por isso, além de mockar os clientes Boto3, também mockamos
`threading.Thread`: assim, quando `start_worker()` instancia e chama
`.start()`, o que roda é um Mock, e o loop infinito nunca chega a
executar de fato em background durante a suíte de testes.

Para testar o CONTEÚDO do `sqs_worker_loop` (sem cair num loop infinito
dentro do próprio teste), usamos um truque: configuramos
`sqs_client.receive_message.side_effect` com uma lista de retornos e,
como último item, a classe `SystemExit`. Como `SystemExit` herda de
`BaseException` (não de `Exception`), ela atravessa o
`except Exception` do loop e interrompe a função exatamente no ponto
que queremos verificar — sem precisar rodar em thread nem usar timeout.

Requisitos para rodar:
    pip install pytest boto3 botocore python-dotenv flask

Executar com:
    pytest test_app.py -v
"""

import os
import sys
import json
import importlib
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


# --- Variáveis de ambiente exigidas pelo app.py na importação ---
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault(
    "AWS_SQS_URL",
    "https://sqs.us-east-1.amazonaws.com/123456789012/test-queue",
)
os.environ.setdefault("AWS_DYNAMODB_TABLE", "test-events-table")


@pytest.fixture
def app_module():
    """
    Importa (ou recarrega) o módulo `app` com:
      - `boto3.Session` mockada (nenhuma credencial/chamada real à AWS);
      - `threading.Thread` mockada (o worker SQS não chega a rodar de
        fato em background durante o import do módulo).
    """
    mock_sqs_client = MagicMock(name="sqs_client")
    mock_dynamodb_client = MagicMock(name="dynamodb_client")

    def fake_client(service_name, *args, **kwargs):
        if service_name == "sqs":
            return mock_sqs_client
        if service_name == "dynamodb":
            return mock_dynamodb_client
        return MagicMock()

    mock_session = MagicMock()
    mock_session.client.side_effect = fake_client

    with patch("boto3.Session", return_value=mock_session), \
         patch("threading.Thread") as mock_thread_cls:

        mock_thread_instance = MagicMock()
        mock_thread_cls.return_value = mock_thread_instance

        if "app" in sys.modules:
            module = importlib.reload(sys.modules["app"])
        else:
            import app as module

        # Expõe os mocks para os testes acessarem/configurarem
        module._mock_sqs_client = mock_sqs_client
        module._mock_dynamodb_client = mock_dynamodb_client
        module._mock_thread_cls = mock_thread_cls
        module._mock_thread_instance = mock_thread_instance

        yield module


@pytest.fixture
def client(app_module):
    """Cliente de teste do Flask (apenas para o /health)."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as test_client:
        yield test_client


def make_sqs_message(body_dict, message_id="msg-1", receipt_handle="receipt-1"):
    """Monta uma mensagem SQS no mesmo formato retornado pela AWS de verdade."""
    return {
        "MessageId": message_id,
        "ReceiptHandle": receipt_handle,
        "Body": json.dumps(body_dict),
    }


# ----------------------------------------------------------------------
# /health
# ----------------------------------------------------------------------

def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


# ----------------------------------------------------------------------
# Inicialização do módulo (clientes Boto3 e thread do worker)
# ----------------------------------------------------------------------

def test_clientes_boto3_sao_mocks_e_nao_conexoes_reais(app_module):
    assert app_module.sqs_client is app_module._mock_sqs_client
    assert app_module.dynamodb_client is app_module._mock_dynamodb_client


def test_worker_thread_e_criada_como_daemon_mas_nunca_roda_de_verdade(app_module):
    """
    Confirma que start_worker() instanciou a Thread com os parâmetros
    corretos (daemon=True, target=sqs_worker_loop) e chamou start() —
    mas como a classe Thread está mockada, nenhuma thread real nem o
    laço infinito chegam a executar durante os testes.
    """
    mock_thread_cls = app_module._mock_thread_cls
    mock_thread_cls.assert_called_once()

    _, kwargs = mock_thread_cls.call_args
    assert kwargs.get("daemon") is True
    assert kwargs.get("target") is app_module.sqs_worker_loop

    app_module._mock_thread_instance.start.assert_called_once()


# ----------------------------------------------------------------------
# process_message
# ----------------------------------------------------------------------

def test_process_message_sucesso(app_module):
    mock_dynamo = app_module._mock_dynamodb_client
    mock_sqs = app_module._mock_sqs_client

    body = {
        "user_id": "user-123",
        "flag_name": "nova-flag",
        "result": True,
        "timestamp": "2026-09-04T12:00:00Z",
    }
    message = make_sqs_message(body, message_id="msg-1", receipt_handle="receipt-1")

    app_module.process_message(message)

    mock_dynamo.put_item.assert_called_once()
    _, kwargs = mock_dynamo.put_item.call_args
    assert kwargs["TableName"] == "test-events-table"
    assert kwargs["Item"]["user_id"] == {"S": "user-123"}
    assert kwargs["Item"]["flag_name"] == {"S": "nova-flag"}
    assert kwargs["Item"]["result"] == {"BOOL": True}
    assert kwargs["Item"]["timestamp"] == {"S": "2026-09-04T12:00:00Z"}

    mock_sqs.delete_message.assert_called_once_with(
        QueueUrl=os.environ["AWS_SQS_URL"],
        ReceiptHandle="receipt-1",
    )


def test_process_message_json_invalido_nao_deleta_da_fila(app_module):
    mock_dynamo = app_module._mock_dynamodb_client
    mock_sqs = app_module._mock_sqs_client

    message = {
        "MessageId": "msg-2",
        "ReceiptHandle": "receipt-2",
        "Body": "{isso nao e um json valido",
    }

    app_module.process_message(message)

    mock_dynamo.put_item.assert_not_called()
    mock_sqs.delete_message.assert_not_called()


def test_process_message_client_error_no_dynamodb_nao_deleta_da_fila(app_module):
    mock_dynamo = app_module._mock_dynamodb_client
    mock_sqs = app_module._mock_sqs_client

    mock_dynamo.put_item.side_effect = ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "x"}},
        "PutItem",
    )

    body = {
        "user_id": "user-123",
        "flag_name": "nova-flag",
        "result": False,
        "timestamp": "2026-09-04T12:00:00Z",
    }
    message = make_sqs_message(body, message_id="msg-3", receipt_handle="receipt-3")

    app_module.process_message(message)

    # Mensagem não é removida da fila: precisa ser reprocessada depois
    mock_sqs.delete_message.assert_not_called()


def test_process_message_campo_obrigatorio_ausente_nao_deleta_da_fila(app_module):
    mock_dynamo = app_module._mock_dynamodb_client
    mock_sqs = app_module._mock_sqs_client

    body = {
        # 'user_id' ausente de propósito -> KeyError -> cai no except genérico
        "flag_name": "nova-flag",
        "result": True,
        "timestamp": "2026-09-04T12:00:00Z",
    }
    message = make_sqs_message(body, message_id="msg-4", receipt_handle="receipt-4")

    app_module.process_message(message)

    mock_dynamo.put_item.assert_not_called()
    mock_sqs.delete_message.assert_not_called()


# ----------------------------------------------------------------------
# sqs_worker_loop
# ----------------------------------------------------------------------

def test_sqs_worker_loop_processa_lote_de_mensagens(app_module):
    mock_sqs = app_module._mock_sqs_client
    mock_dynamo = app_module._mock_dynamodb_client

    body = {
        "user_id": "user-1",
        "flag_name": "flag-x",
        "result": True,
        "timestamp": "2026-09-04T12:00:00Z",
    }
    message = make_sqs_message(body, message_id="msg-1", receipt_handle="receipt-1")

    mock_sqs.receive_message.side_effect = [
        {"Messages": [message]},
        SystemExit,  # interrompe o "while True" após a 1ª iteração, só no teste
    ]

    with pytest.raises(SystemExit):
        app_module.sqs_worker_loop()

    mock_sqs.receive_message.assert_any_call(
        QueueUrl=os.environ["AWS_SQS_URL"],
        MaxNumberOfMessages=10,
        WaitTimeSeconds=20,
    )
    mock_dynamo.put_item.assert_called_once()
    mock_sqs.delete_message.assert_called_once()


def test_sqs_worker_loop_sem_mensagens_apenas_continua(app_module):
    mock_sqs = app_module._mock_sqs_client
    mock_dynamo = app_module._mock_dynamodb_client

    mock_sqs.receive_message.side_effect = [
        {"Messages": []},
        SystemExit,
    ]

    with pytest.raises(SystemExit):
        app_module.sqs_worker_loop()

    mock_dynamo.put_item.assert_not_called()


def test_sqs_worker_loop_client_error_no_receive_aguarda_e_continua(app_module, monkeypatch):
    mock_sqs = app_module._mock_sqs_client
    mock_sleep = MagicMock()
    monkeypatch.setattr(app_module.time, "sleep", mock_sleep)

    mock_sqs.receive_message.side_effect = [
        ClientError({"Error": {"Code": "Throttling", "Message": "x"}}, "ReceiveMessage"),
        SystemExit,
    ]

    with pytest.raises(SystemExit):
        app_module.sqs_worker_loop()

    mock_sleep.assert_called_once_with(10)

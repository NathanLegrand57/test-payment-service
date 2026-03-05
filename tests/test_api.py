import os
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import engine, SessionLocal
from app.models import Payment, Base
from jose import jwt
import time

# Use a separate test database
os.environ["DATABASE_URL"] = "sqlite:///./test_temp.db"
os.environ["STRIPE_API_KEY"] = "sk_test_mock"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_mock"
os.environ["JWT_SECRET"] = "test-secret-key"

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def create_test_token():
    payload = {
        "iss": "laravel-app",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    return jwt.encode(payload, "test-secret-key", algorithm="HS256")


def test_create_payment_unauthorized():
    payload = {
        "order_id": "123",
        "amount": 1000,
        "currency": "eur"
    }
    # No Authorization header should result in 422 because it's required by Header(...)
    response = client.post("/payments", json=payload)
    assert response.status_code == 422


def test_create_payment_invalid_token():
    payload = {
        "order_id": "123",
        "amount": 1000,
        "currency": "eur"
    }
    headers = {"Authorization": "Bearer invalid-token"}
    response = client.post("/payments", json=payload, headers=headers)
    assert response.status_code == 401


def test_create_payment_success(mocker):
    # Mock Stripe call
    mock_intent = mocker.Mock()
    mock_intent.id = "pi_mock_123"
    mock_intent.client_secret = "secret_mock_123"
    mocker.patch("app.routes.create_payment", return_value=mock_intent)

    headers = {"Authorization": f"Bearer {create_test_token()}"}
    payload = {
        "order_id": "ORDER_TEST_1",
        "amount": 5000,
        "currency": "eur"
    }

    response = client.post("/payments", json=payload, headers=headers)

    assert response.status_code == 200
    assert response.json()["client_secret"] == "secret_mock_123"

    # Check DB
    db = SessionLocal()
    payment = db.query(Payment).filter_by(order_id="ORDER_TEST_1").first()
    assert payment is not None
    assert payment.amount == 5000
    db.close()


def test_get_existing_payment(mocker):
    # Setup: add a payment to DB
    db = SessionLocal()
    existing_payment = Payment(
        id="pi_existing_123",
        order_id="ORDER_EXISTING",
        amount=2000,
        currency="eur",
        status="created"
    )
    db.add(existing_payment)
    db.commit()
    db.close()

    headers = {"Authorization": f"Bearer {create_test_token()}"}
    payload = {
        "order_id": "ORDER_EXISTING",
        "amount": 2000
    }

    response = client.post("/payments", json=payload, headers=headers)

    assert response.status_code == 200
    assert response.json()["payment_id"] == "pi_existing_123"
    assert response.json()["status"] == "created"


def test_refund_success(mocker):
    # Setup database with a payment to refund
    db = SessionLocal()
    payment = Payment(
        id="pi_to_refund",
        order_id="ORDER_REFUND",
        amount=3000,
        currency="eur",
        status="created"
    )
    db.add(payment)
    db.commit()
    db.close()

    # Mock Stripe refund
    mocker.patch("app.routes.refund_payment", return_value=None)

    headers = {"Authorization": f"Bearer {create_test_token()}"}
    response = client.post(
        "/refund", params={"order_id": "ORDER_REFUND"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "refunded"

    # Verify DB update
    db = SessionLocal()
    updated_payment = db.query(Payment).filter_by(
        order_id="ORDER_REFUND").first()
    assert updated_payment.status == "refunded"
    db.close()


def test_refund_no_payment(mocker):
    headers = {"Authorization": f"Bearer {create_test_token()}"}
    response = client.post(
        "/refund", params={"order_id": "NON_EXISTENT"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["message"] == "Nothing to refund"

import unittest
from fastapi import FastAPI,APIRouter
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from backend.errors import install_handlers
from backend.knowledge.router import CredentialSafeRoute

class DatabaseFailure(Exception):
    sqlstate='57014'

class ErrorFeedbackTests(unittest.TestCase):
    def test_database_timeout_is_actionable_and_logs_a_matching_safe_reference(self):
        app=FastAPI();router=APIRouter(route_class=CredentialSafeRoute)
        @router.get('/failure')
        def failure():raise OperationalError('private statement',{'password':'private-password'},DatabaseFailure('private-url-secret'))
        app.include_router(router)
        with self.assertLogs('wenjin.errors',level='ERROR') as records,TestClient(app) as client:
            response=client.get('/failure')
        body=response.json();self.assertEqual(response.status_code,503);self.assertEqual(body['code'],'DATABASE_TIMEOUT')
        self.assertIn('查询超时',body['detail']);self.assertEqual(response.headers['x-request-id'],body['request_id'])
        self.assertIn(body['request_id'],' '.join(records.output))
        for value in ('private-password','private-url-secret','private statement'):
            self.assertNotIn(value,response.text+' '.join(records.output))

    def test_unexpected_error_returns_json_instead_of_an_empty_or_html_response(self):
        app=FastAPI();install_handlers(app)
        @app.get('/failure')
        def failure():raise RuntimeError('private-account-data')
        with self.assertLogs('wenjin.errors',level='ERROR'),TestClient(app,raise_server_exceptions=False) as client:
            response=client.get('/failure')
        self.assertEqual(response.status_code,500);self.assertEqual(response.json()['code'],'SERVER_ERROR')
        self.assertNotIn('private-account-data',response.text)

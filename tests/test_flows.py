"""Critical operational flow tests: persisted state, access control, CSRF, stages and exports."""
import importlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


def setup():
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    tmp=tempfile.TemporaryDirectory()
    os.environ['LPC_INSTANCE']=tmp.name
    os.environ['LPC_SECRET_KEY']='test-key-only-not-for-production'
    import main
    importlib.reload(main)
    main.bootstrap('Admin Teste','admin@example.com','super-secret-pass-123')
    client=TestClient(main.app,follow_redirects=True)
    return tmp,main,client


def login(client):
    p=client.get('/login')
    # Cookie session contains CSRF, read from rendered form using a tiny regex.
    import re
    token=re.search(r'name="csrf" value="([^"]+)',p.text).group(1)
    r=client.post('/login',data={'csrf':token,'email':'admin@example.com','password':'super-secret-pass-123'})
    assert r.status_code==200 and 'Visão geral' in r.text
    token=re.search(r'name="csrf-token" content="([^"]+)',r.text).group(1)
    return token


def test_full_happy_path():
    tmp,mod,c=setup()
    try:
        token=login(c)
        r=c.get('/clients');assert 'Nenhum cliente encontrado' in r.text
        r=c.post('/clients/new',data={'csrf':token,'name':'Cliente Teste','phone':'62999999999','vehicle_model':'Lancer GT','owner_id':'1'})
        assert r.status_code==200 and 'Cliente Teste' in r.text
        with mod.db() as con:
            person=mod.qone(con,'SELECT * FROM clients WHERE name=?',('Cliente Teste',));cid=person['id']
        r=c.post('/clients/new',data={'csrf':token,'name':'Duplicado','phone':'(62)99999-9999','owner_id':'1'})
        assert 'Possível duplicidade' in r.text
        r=c.post(f'/clients/{cid}/activity',data={'csrf':token,'description':'Primeiro contato pelo WhatsApp'})
        assert 'Primeiro contato' in r.text
        r=c.post('/opportunities/new',data={'csrf':token,'client_id':cid,'product':'Coletor 4x2x1','value':'1499,00','stage':'Novo contato','owner_id':1})
        assert r.status_code==200
        with mod.db() as con:
            oid=mod.qone(con,'SELECT id FROM opportunities')['id']
        r=c.patch(f'/api/opportunities/{oid}/stage',json={'stage':'Orçamento enviado'},headers={'x-csrf-token':token})
        assert r.json().get('ok')
        r=c.post('/followups/new',data={'csrf':token,'client_id':cid,'product':'Coletor 4x2x1','quoted_value':'1499','due_at':'2026-12-10T14:30','owner_id':1,'category':'Promoção','status':'Pendente'})
        assert r.status_code==200
        r=c.post('/orders/new',data={'csrf':token,'client_id':cid,'order_number':'LP-001','total':'1499','shipping':'50','discount':'0','payment_status':'Aprovado','operational_status':'Em produção','shipping_status':'Não enviado','product':'Coletor','quantity':1,'unit_price':'1449','owner_id':1})
        assert 'LP-001' in r.text
        r=c.post('/tasks/new',data={'csrf':token,'title':'Conferir peça','priority':'Alta','assignee_id':'1','client_id':cid})
        assert 'Conferir peça' in r.text
        r=c.get('/');assert 'R$ 1.499,00' in r.text
        r=c.get('/clients/'+str(cid));assert 'Coletor 4x2x1' in r.text
        r=c.get('/export/orders');assert r.status_code==200 and 'LP-001' in r.text
        r=c.get('/integrations');assert 'Desconectado' in r.text and 'nenhum provedor' in r.text
        with mod.db() as con:
            assert mod.qone(con,"SELECT count(*) n FROM audit_logs")['n']>=6
    finally:tmp.cleanup()


def test_permissions_and_csrf():
    tmp,mod,c=setup()
    try:
        token=login(c)
        r=c.post('/clients/new',data={'csrf':'invalid','name':'X','phone':'62999999998'})
        assert r.status_code==403
        with mod.db() as con:
            con.execute('INSERT INTO users (name,email,password_hash,role) VALUES (?,?,?,?)',('Logistica','log@example.com',mod.hash_password('twelve-char-pass'),'logistica'))
        c.post('/logout',data={'csrf':token})
        p=c.get('/login');import re
        token=re.search(r'name="csrf" value="([^"]+)',p.text).group(1)
        c.post('/login',data={'csrf':token,'email':'log@example.com','password':'twelve-char-pass'})
        r=c.get('/clients');assert r.status_code==403
        r=c.get('/orders');assert r.status_code==200
        r=c.get('/team');assert r.status_code==403
    finally:tmp.cleanup()

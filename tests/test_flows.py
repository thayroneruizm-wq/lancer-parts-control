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
        r=c.get('/');assert 'LP-001' in r.text and 'Conferir peça' in r.text
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


def test_order_with_multiple_items():
    tmp,mod,c=setup()
    try:
        token=login(c)
        c.post('/clients/new',data={'csrf':token,'name':'Cliente Multi','phone':'62988887777','owner_id':'1'})
        with mod.db() as con:
            cid=mod.qone(con,'SELECT id FROM clients WHERE name=?',('Cliente Multi',))['id']
        # total em branco -> calculado: (2*100 + 1*50,50) - 10 + 20 = 260,50
        r=c.post('/orders/new',data={'csrf':token,'client_id':cid,'order_number':'LP-MULTI','total':'0','shipping':'20','discount':'10',
            'product':['Coletor','Junta',''],'sku':['COL-1','JT-1',''],'quantity':['2','1',''],'unit_price':['100','50,50',''],'owner_id':1})
        assert r.status_code==200 and 'Coletor' in r.text and 'Junta' in r.text
        with mod.db() as con:
            o=mod.qone(con,'SELECT * FROM orders WHERE order_number=?',('LP-MULTI',))
            items=mod.qall(con,'SELECT * FROM order_items WHERE order_id=? ORDER BY id',(o['id'],))
        assert len(items)==2 and o['total_cents']==26050
        # edição: remove um item
        r=c.post(f"/orders/{o['id']}/edit",data={'csrf':token,'client_id':cid,'order_number':'LP-MULTI','total':'300','shipping':'0','discount':'0',
            'product':['Coletor'],'sku':['COL-1'],'quantity':['3'],'unit_price':['100'],'owner_id':1})
        assert r.status_code==200
        with mod.db() as con:
            items=mod.qall(con,'SELECT * FROM order_items WHERE order_id=?',(o['id'],))
            logs=mod.qall(con,"SELECT action FROM audit_logs WHERE entity='orders' AND entity_id=?",(o['id'],))
        assert len(items)==1 and items[0]['quantity']==3
        assert any(l['action']=='items' for l in logs)
    finally:
        tmp.cleanup()


def test_whatsapp_helpers():
    tmp,mod,c=setup()
    try:
        assert mod.wa_link('(62) 99999-9999')=='https://wa.me/5562999999999'
        assert mod.wa_link('+55 62 3333-4444')=='https://wa.me/556233334444'
        assert mod.wa_link('')=='' and mod.wa_link('123')==''
        assert 'text=' in mod.wa_link('62999999999','Olá')
        token=login(c)
        r=c.post('/settings',data={'csrf':token,'store_whatsapp':'(62) 98888-7777'})
        assert r.status_code==200 and '(62) 98888-7777' in r.text
        assert c.get('/static/logo.png').status_code==200
    finally:
        tmp.cleanup()


def test_production_flow_and_sold_count():
    tmp,mod,c=setup()
    try:
        token=login(c)
        c.post('/clients/new',data={'csrf':token,'name':'Cliente Prod','phone':'62977776666','owner_id':'1'})
        with mod.db() as con:
            cid=mod.qone(con,'SELECT id FROM clients WHERE name=?',('Cliente Prod',))['id']
            pid=mod.qone(con,"SELECT id FROM products WHERE name='Coletor Cat Delete'")['id']
        r=c.post('/production/new',data={'csrf':token,'product_id':pid,'qty_planned':'10','owner_id':'1'})
        assert r.status_code==200 and 'LT-' in r.text
        with mod.db() as con:
            b=mod.qone(con,'SELECT * FROM production_batches')
        bid=b['id']
        # apontar 4 -> Em produção
        c.post(f'/production/{bid}/finish',data={'csrf':token,'qty':'4'})
        # acima do planejado é rejeitado
        r=c.post(f'/production/{bid}/finish',data={'csrf':token,'qty':'99'})
        assert 'Quantidade inválida' in r.text
        # venda aprovada de 3 unidades
        c.post('/orders/new',data={'csrf':token,'client_id':cid,'order_number':'LP-PROD','total':'0','shipping':'0','discount':'0','payment_status':'Aprovado',
            'product':['Coletor Cat Delete'],'quantity':['3'],'unit_price':['1000'],'owner_id':1})
        # pedido pendente não conta como vendido
        c.post('/orders/new',data={'csrf':token,'client_id':cid,'order_number':'LP-PEND','total':'0','shipping':'0','discount':'0','payment_status':'Pendente',
            'product':['Coletor Cat Delete'],'quantity':['5'],'unit_price':['1000'],'owner_id':1})
        with mod.db() as con:
            items,totals=mod.production_summary(con)
        assert totals=={'in_progress':6,'finished':4,'sold':3,'stock':1}, totals
        # concluir lote: +6 -> Finalizado
        c.post(f'/production/{bid}/finish',data={'csrf':token,'qty':'6'})
        with mod.db() as con:
            b=mod.qone(con,'SELECT * FROM production_batches WHERE id=?',(bid,))
        assert b['status']=='Finalizado' and b['qty_finished']==10 and b['finished_at']
        # correção negativa reabre o lote
        c.post(f'/production/{bid}/finish',data={'csrf':token,'qty':'-2'})
        with mod.db() as con:
            b=mod.qone(con,'SELECT * FROM production_batches WHERE id=?',(bid,))
        assert b['status']=='Em produção' and b['qty_finished']==8 and b['finished_at'] is None
        assert c.get('/production').status_code==200 and c.get(f'/production/{bid}').status_code==200
    finally:
        tmp.cleanup()


def _seed_orders(mod):
    from datetime import datetime,timedelta,timezone
    now=mod.now()
    def iso(dt):return dt.replace(tzinfo=None).isoformat(timespec='minutes')
    def ago(days):return (datetime.now(timezone.utc)-timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    ids={}
    with mod.db() as c:
        c.execute("INSERT INTO clients(name,phone,phone_normalized,owner_id) VALUES('Cliente Controle','(62) 99999-9999','62999999999',1)")
        def order(num,pay,op,ship='Não enviado',age=0,due=None,tracking=''):
            oid=c.execute("INSERT INTO orders(client_id,order_number,payment_status,operational_status,shipping_status,created_at,due_at,tracking_code,owner_id) VALUES(1,?,?,?,?,?,?,?,1)",(num,pay,op,ship,ago(age),due,tracking)).lastrowid
            c.execute("INSERT INTO order_items(order_id,product,quantity) VALUES(?,'Coletor Cat Delete',2)",(oid,));ids[num]=oid
        order('PAGO','Aprovado','Pagamento aprovado',age=3)
        order('PEND','Pendente','Aguardando pagamento',age=4)
        order('ENV','Aprovado','Enviado','Em trânsito',age=2)
        order('ATRASO','Aprovado','Em produção',due=iso(now-timedelta(days=1)))
        order('OCORR','Aprovado','Ocorrência logística','Ocorrência')
        order('ENTREGUE','Aprovado','Entregue','Entregue',age=9,tracking='BR1')
        c.execute("INSERT INTO followups(client_id,product,due_at,priority,owner_id,status) VALUES(1,'Coletor',?,'Média',1,'Pendente')",(iso(now-timedelta(days=2)),))
        c.execute("INSERT INTO tasks(title,due_at,priority,assignee_id,recurrence) VALUES('Conferir estoque',?,'Alta',1,'Semanal')",(iso(now-timedelta(hours=5)),))
    return ids


def test_dashboard_without_revenue_and_with_control():
    tmp,mod,c=setup()
    try:
        token=login(c);_seed_orders(mod)
        r=c.get('/')
        assert r.status_code==200
        for gone in ('Faturamento','Ticket médio','Pedidos recentes'):assert gone not in r.text
        for here in ('Controle de pedidos em aberto','O QUE PRECISA DA MINHA ATENÇÃO','Cronograma dos próximos dias'):assert here in r.text
        assert 'Ocorrência logística no pedido #OCORR' in r.text
        r=c.get('/controle-pedidos');assert '#PAGO' in r.text and '#ENTREGUE' not in r.text
        r=c.get('/controle-pedidos?stage=entregues');assert '#ENTREGUE' in r.text
        r=c.get('/agenda');assert 'Atrasados' in r.text and 'Central de avisos'.upper() in r.text
    finally:
        tmp.cleanup()


def test_alerts_levels_and_quick_actions():
    tmp,mod,c=setup()
    try:
        token=login(c);ids=_seed_orders(mod)
        with mod.db() as con:
            u=mod.record(mod.qone(con,'SELECT * FROM users WHERE id=1'));alerts=mod.build_alerts(con,u)
        titles=' | '.join(a['title'] for a in alerts)
        assert 'Prazo do pedido #ATRASO vencido' in titles and 'Pedido #PEND aguardando pagamento' in titles
        assert 'Pedido #ENV enviado sem código de rastreio' in titles and '#ENTREGUE' not in titles
        assert alerts[0]['level']==0
        # pedido pendente não pode ser enviado
        r=c.post(f"/orders/{ids['PEND']}/quick",data={'csrf':token,'action':'shipped','next':'/controle-pedidos'})
        assert 'Pagamento ainda não aprovado' in r.text
        # enviar pedido pago com rastreio
        r=c.post(f"/orders/{ids['PAGO']}/quick",data={'csrf':token,'action':'shipped','tracking_code':'BR999','next':'/controle-pedidos'})
        with mod.db() as con:
            o=mod.record(mod.qone(con,'SELECT * FROM orders WHERE id=?',(ids['PAGO'],)))
        assert o['operational_status']=='Enviado' and o['tracking_code']=='BR999' and o['packed']==1
        c.post(f"/orders/{ids['PAGO']}/quick",data={'csrf':token,'action':'notified'})
        c.post(f"/orders/{ids['PAGO']}/quick",data={'csrf':token,'action':'delivered'})
        with mod.db() as con:
            o=mod.record(mod.qone(con,'SELECT * FROM orders WHERE id=?',(ids['PAGO'],)))
        ctl=mod.order_control(o)
        assert ctl['stage']=='Entregue' and ctl['notified']
        # CSRF e redirecionamento seguro
        assert c.post(f"/orders/{ids['PAGO']}/quick",data={'csrf':'x','action':'packed'}).status_code==403
        assert mod.safe_next('//evil.com','/controle-pedidos')=='/controle-pedidos'
    finally:
        tmp.cleanup()


def test_recurring_task_creates_next():
    tmp,mod,c=setup()
    try:
        token=login(c);_seed_orders(mod)
        with mod.db() as con:
            tid=mod.qone(con,"SELECT id FROM tasks WHERE title='Conferir estoque'")['id']
        c.post(f'/tasks/{tid}/toggle',data={'csrf':token})
        with mod.db() as con:
            rows=mod.qall(con,"SELECT * FROM tasks WHERE title='Conferir estoque' ORDER BY id")
        assert len(rows)==2 and rows[0]['status']=='Concluída' and rows[1]['status']=='Pendente'
        assert mod.parse_dt(rows[1]['due_at'])>mod.now()
        assert mod.next_occurrence('2026-01-31T10:00','Mensal')>=mod.now().replace(tzinfo=None).isoformat(timespec='minutes')
    finally:
        tmp.cleanup()


def test_dashboard_attention_new_future_manual_dismiss_and_delete():
    tmp,mod,c=setup()
    try:
        token=login(c)
        # Uma tarefa recém-criada, mesmo sem prazo, deve aparecer.
        c.post('/tasks/new',data={'csrf':token,'title':'Conferir nova vitrine','priority':'Média','assignee_id':'1'})
        with mod.db() as con:
            task=mod.qone(con,"SELECT * FROM tasks WHERE title='Conferir nova vitrine'")
            uid=mod.record(mod.qone(con,"SELECT id,name,email,role,active FROM users WHERE id=1"))
            keys=[a['key'] for a in mod.build_alerts(con,uid)]
            assert f"task:{task['id']}" in keys
        assert 'Conferir nova vitrine' in c.get('/').text

        # Aviso manual aparece imediatamente no Dashboard e é persistido.
        result=c.post('/alerts/new',data={'csrf':token,'title':'Revisar promoções','detail':'Ver preços da linha de coletores','priority':'Urgente'})
        assert result.status_code==200
        with mod.db() as con:
            note=mod.qone(con,"SELECT * FROM attention_notes WHERE title='Revisar promoções'")
            assert note and note['status']=='Pendente'
            keys=[a['key'] for a in mod.build_alerts(con,uid)]
            assert f"note:{note['id']}" in keys
        assert 'Revisar promoções' in c.get('/').text

        # Dispensar é individual: o registro de origem continua existindo.
        r=c.post('/alerts/dismiss',data={'csrf':token,'key':f"task:{task['id']}",'next':'/'})
        assert r.status_code==200
        with mod.db() as con:
            assert mod.qone(con,'SELECT id FROM tasks WHERE id=?',(task['id'],))
            assert f"task:{task['id']}" not in [a['key'] for a in mod.build_alerts(con,uid)]
        assert c.post('/alerts/dismiss',data={'csrf':'invalid','key':f"note:{note['id']}"}).status_code==403
        assert c.post('/alerts/dismiss',data={'csrf':token,'key':'order:999:arbitrary'}).status_code==404

        # Atualização de registro manual devolve o aviso à lista.
        r=c.post('/alerts/dismiss',data={'csrf':token,'key':f"note:{note['id']}",'next':'/agenda'})
        assert r.status_code==200
        with mod.db() as con:
            assert f"note:{note['id']}" not in [a['key'] for a in mod.build_alerts(con,uid)]
        r=c.post(f"/alerts/{note['id']}/edit",data={'csrf':token,'title':'Revisar promoções agora','detail':'Verificar estoque','priority':'Alta'})
        assert r.status_code==200
        with mod.db() as con:
            assert f"note:{note['id']}" in [a['key'] for a in mod.build_alerts(con,uid)]
        r=c.post(f"/alerts/{note['id']}/delete",data={'csrf':token})
        assert r.status_code==200
        with mod.db() as con:
            assert mod.qone(con,'SELECT * FROM attention_notes WHERE id=?',(note['id'],)) is None
            assert mod.qone(con,"SELECT count(*) n FROM audit_logs WHERE entity='attention_notes' AND action='delete'")['n']==1

        # Excluir tarefa retira a atenção e deixa rastro de auditoria.
        assert c.post(f"/tasks/{task['id']}/delete",data={'csrf':token}).status_code==200
        with mod.db() as con:
            assert mod.qone(con,'SELECT id FROM tasks WHERE id=?',(task['id'],)) is None
    finally:
        tmp.cleanup()


def test_future_followup_and_scoped_dismissal():
    tmp,mod,c=setup()
    try:
        token=login(c)
        from datetime import timedelta
        due=(mod.now()+timedelta(days=21)).replace(tzinfo=None).isoformat(timespec='minutes')
        c.post('/clients/new',data={'csrf':token,'name':'Cliente Futuro','phone':'62999980001','owner_id':'1'})
        with mod.db() as con:
            cid=mod.qone(con,"SELECT id FROM clients WHERE name='Cliente Futuro'")['id']
        c.post('/followups/new',data={'csrf':token,'client_id':cid,'product':'Intake Full Inox','due_at':due,'priority':'Média','owner_id':'1'})
        with mod.db() as con:
            f=mod.qone(con,'SELECT * FROM followups WHERE client_id=?',(cid,))
            admin=mod.record(mod.qone(con,'SELECT id,name,email,role,active FROM users WHERE id=1'))
            alerts=mod.build_alerts(con,admin)
            matching=[a for a in alerts if a['key']==f"followup:{f['id']}"]
            assert len(matching)==1 and matching[0]['level']==2
            con.execute("INSERT INTO users (name,email,password_hash,role) VALUES(?,?,?,?)",
                        ('Vendedor dois','two@example.com',mod.hash_password('some-strong-password-234'),'vendedor'))
            other=mod.record(mod.qone(con,"SELECT id,name,email,role,active FROM users WHERE email='two@example.com'"))
            assert f"followup:{f['id']}" not in [a['key'] for a in mod.build_alerts(con,other)]
        assert 'Intake Full Inox' in c.get('/').text
        assert c.post('/alerts/dismiss',data={'csrf':token,'key':f"followup:{f['id']}",'next':'/agenda'}).status_code==200
        with mod.db() as con:
            assert mod.qone(con,'SELECT id FROM followups WHERE id=?',(f['id'],))
        assert c.post(f"/followups/{f['id']}/delete",data={'csrf':token}).status_code==200
        with mod.db() as con:
            assert mod.qone(con,'SELECT id FROM followups WHERE id=?',(f['id'],)) is None
    finally:
        tmp.cleanup()

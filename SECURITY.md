# Segurança

## Estado atual

Versão 0.1: MVP operacional **não homologado para produção**. Não há conexão com Nuvemshop, Melhor Envio ou WhatsApp; dados criados no sistema ficam somente no banco local configurado. Este código pode ser público, mas **não hospede dados pessoais reais numa instância exposta na internet** antes da revisão de segurança e LGPD.

## Não publicar segredos

O repositório contém `.env.example`, nunca um `.env` real. Defina `LPC_SECRET_KEY` em variáveis protegidas do servidor/gerenciador de segredos. A pasta `instance/` armazena o banco SQLite e a chave de assinatura local e deve permanecer fora do Git e da imagem Docker.

## Antes da entrada em produção

Habilitar HTTPS e cookies seguros; limitação de tentativas de login e MFA; monitoramento; backup e restauração testados; revisão de autorização por função; políticas de retenção/exportação/exclusão de dados pessoais; dependências atualizadas; teste de carga e auditoria de segurança.

Se descobrir uma vulnerabilidade, **não publique credenciais nem dados de clientes em issues públicas**. Utilize um canal privado e, quando configurado, o recurso **Private vulnerability reporting** do GitHub.

# Publicar este projeto no GitHub

Este é um projeto de **software de gestão interna**. Um repositório GitHub público expõe o código-fonte, **não coloca a aplicação online** nem conecta a Nuvemshop, WhatsApp ou Melhor Envio.

## 1. Criar repositório público na conta GitHub

1. Acesse https://github.com/new com a conta correta.
2. Nome: `lancer-parts-control`.
3. Visibilidade: **Public** (atenção: qualquer pessoa poderá ler o código).
4. **Não** marque "Add a README file", ".gitignore" ou "Choose a license": estes arquivos já existem no projeto.
5. Clique em **Create repository**.

Se sua conta GitHub estiver conectada ao ChatGPT, envie aqui o link do repositório criado. Poderemos então publicar os arquivos diretamente nele usando a conexão autorizada, sem copiar senha ou token.

## 2. Opção de publicação pelo terminal (sem integração ChatGPT)

Se o projeto já estiver em um diretório Git com um commit inicial, execute na raiz:

```bash
git remote add origin https://github.com/SEU-USUARIO/lancer-parts-control.git
git push -u origin main
```

Substitua `SEU-USUARIO` pelo nome da sua conta. Ao realizar a autenticação, use o método seguro recomendado pelo GitHub (por exemplo, Git Credential Manager ou SSH); **nunca coloque token no código nem na URL do remote**.

Se o projeto veio apenas de um ZIP de arquivos, inicialize o Git primeiro:

```bash
git init -b main
git add .
git commit -m "Initial MVP: Lancer Parts Control"
```

Depois rode `git remote add ...` e `git push ...`.

## 3. O que entra no repositório

- Código FastAPI/Jinja2/JavaScript/CSS, SQL e templates.
- Dockerfile, Compose, testes e GitHub Actions CI.
- Configuração de ambiente **exemplo** (`.env.example`), sem segredos reais.
- Documentação, limitações, próximos passos.

**Nunca envie**: `.env` preenchido, arquivos de banco em `instance/`, tokens OAuth, senhas, CPF/telefone/endereço real de clientes ou backups.

## 4. Depois de publicar

- No GitHub, confira a guia **Actions** e o resultado dos testes.
- Para ter o sistema acessível pelo navegador fora da sua rede, configure hospedagem (servidor e HTTPS), cofre de segredos e backup, seguindo o README.
- **Não exponha este MVP diretamente à internet com clientes reais** antes de implementar a proteção adicional de login e os controles indicados no README.
- Integrações estão desconectadas até implementação e autorização oficial.

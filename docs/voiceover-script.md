Olá, meu nome é Mariana Nascimento, e nesta apresentação vou demonstrar a solução desenvolvida para o desafio técnico de AI Hardcore Engineer da Getnet.

O objetivo do desafio foi construir um sistema de atendimento inteligente, baseado em uma arquitetura multiagente, capaz de interpretar solicitações de usuários, coordenar agentes especializados e produzir respostas contextualizadas.

A solução contempla três agentes principais: um responsável pelo roteamento das solicitações, outro pelo conhecimento dos produtos e serviços da Getnet, utilizando RAG, e um terceiro dedicado ao suporte ao cliente, com ferramentas para consulta de informações.

Além desses componentes, foram implementados mecanismos de segurança, observabilidade e encaminhamento para atendimento humano.

O projeto foi desenvolvido com o apoio de um assistente de programação com inteligência artificial, seguindo uma abordagem de desenvolvimento orientado por especificações. Meu papel envolveu a definição das restrições, a revisão das decisões arquiteturais e a validação dos resultados.

Vou apresentar a arquitetura, explicar as principais escolhas técnicas e, por fim, demonstrar as evidências de funcionamento e os pontos de evolução da solução.

Começando pela arquitetura.

Toda solicitação chega à aplicação por meio de um endpoint HTTP, implementado com FastAPI.

Antes do processamento, a mensagem passa por mecanismos de validação e segurança, conhecidos como guardrails.

Em seguida, ela é encaminhada ao Router Agent, responsável por identificar a intenção do usuário e determinar qual agente especializado deverá executar a tarefa.

O roteador pode selecionar um único agente ou coordenar uma sequência de agentes, dependendo da complexidade da solicitação.

O primeiro agente especializado é o Knowledge Agent, responsável por responder perguntas relacionadas aos produtos e serviços da Getnet.

O segundo é o Customer Support Agent, que trata solicitações específicas de clientes utilizando ferramentas de consulta.

Como funcionalidade adicional, também foi implementado um Escalation Agent, responsável por simular o encaminhamento de situações que exigem atendimento humano.

A comunicação entre os agentes utiliza estruturas tipadas com Pydantic, chamadas Agent Task e Agent Result.

A primeira representa a tarefa enviada ao agente, incluindo a mensagem, a identificação do usuário, a intenção e o contexto disponível.

A segunda contém o resultado da execução, incluindo resposta, fontes consultadas, ferramentas utilizadas e status.

Quando uma solicitação envolve múltiplos agentes, a execução acontece de forma sequencial. O resultado do primeiro agente é incorporado ao contexto recebido pelo próximo.

Ao final, uma camada chamada Personality consolida os resultados em uma resposta única, mantendo consistência na linguagem utilizada.

Essa separação de responsabilidades facilita a manutenção, os testes e a evolução da arquitetura.

Agora, entrando no funcionamento do RAG.

RAG significa Retrieval-Augmented Generation, ou geração aumentada por recuperação.

Essa abordagem permite que o modelo de linguagem utilize informações recuperadas de uma base de conhecimento, em vez de depender exclusivamente do conhecimento adquirido durante seu treinamento.

Na solução, o processo foi dividido em duas etapas principais: ingestão e consulta.

Durante a ingestão, um crawler coleta informações de páginas oficiais da Getnet, incluindo o site brasileiro, o site global e conteúdos de perguntas frequentes.

O texto é extraído, organizado e armazenado em um snapshot versionado, permitindo reproduzir a base utilizada nos testes.

Foram coletadas quarenta e seis páginas, posteriormente divididas em cento e noventa e cinco trechos menores, conhecidos como chunks.

Esses trechos são convertidos em representações numéricas, chamadas embeddings, utilizando um modelo multilíngue executado localmente.

Os vetores são armazenados no ChromaDB, juntamente com os textos originais e as informações de origem.

Quando o usuário faz uma pergunta, ela também é transformada em um vetor.

O sistema compara essa representação com os vetores armazenados, identificando os documentos semanticamente mais relevantes.

A escolha de um modelo multilíngue foi importante porque parte do conteúdo da Getnet está em português, enquanto os cenários do desafio foram apresentados em inglês.

Entretanto, durante a validação, surgiu uma limitação interessante.

A busca exclusivamente vetorial apresentou dificuldades para recuperar corretamente informações sobre produtos com nomes específicos, como Get Clássica e Get Smart.

Para melhorar esse comportamento, foi implementada uma estratégia de recuperação híbrida.

Ela combina a busca semântica com o BM vinte e cinco, um algoritmo que prioriza correspondências textuais relevantes.

Os resultados das duas abordagens são combinados por meio de Reciprocal Rank Fusion, uma técnica que considera a posição de cada documento nos diferentes rankings.

Essa combinação melhorou a recuperação das páginas relacionadas aos produtos.

Depois da recuperação, os trechos selecionados são fornecidos ao modelo de linguagem, que utiliza esse contexto para elaborar a resposta.

As referências são construídas diretamente a partir dos documentos recuperados, reduzindo o risco de apresentar fontes inexistentes.

Passando agora para o atendimento ao cliente.

O Customer Support Agent foi desenvolvido para lidar com perguntas que dependem de informações específicas de um estabelecimento.

Foram implementadas ferramentas para consultar o status de transações, a programação de recebimentos e a situação de terminais de pagamento.

Como o desafio não disponibiliza acesso aos sistemas internos da Getnet, essas ferramentas utilizam uma base de dados simulada e determinística.

Isso permite demonstrar a execução das funções e a integração com os agentes, sem utilizar informações bancárias reais.

O sistema também identifica explicitamente essas respostas como dados simulados.

Um exemplo interessante de colaboração entre agentes ocorre quando um cliente informa que sua maquininha não consegue se conectar à internet.

Nesse cenário, o Customer Support Agent consulta a situação do terminal simulado.

Em seguida, o resultado pode ser encaminhado ao Knowledge Agent, que recupera orientações técnicas disponíveis na base de conhecimento.

Por fim, a camada Personality consolida o diagnóstico e as orientações em uma resposta coerente.

Esse fluxo demonstra a principal proposta da arquitetura: combinar especializações diferentes para resolver solicitações mais complexas.

Sobre as decisões de tecnologia, uma das restrições adotadas foi evitar APIs pagas e permitir a execução local em um MacBook com processador M1 e oito gigabytes de memória.

Por isso, o modelo padrão escolhido foi o Llama três ponto dois, com três bilhões de parâmetros, executado por meio do Ollama.

Essa decisão representa um compromisso entre custo, capacidade computacional e qualidade das respostas.

O projeto utiliza um cliente compatível com a API da OpenAI, permitindo substituir o provedor por meio de configurações de ambiente.

A API foi implementada com FastAPI, enquanto o Pydantic garante a validação das entradas e a padronização das mensagens entre os agentes.

Também foi adotada uma orquestração própria, sem dependência de frameworks como LangChain.

Considerando o tamanho do projeto, essa escolha permitiu manter o fluxo mais simples, explícito e fácil de testar.

Para aumentar a confiabilidade, as decisões estruturadas do modelo são validadas pelo código.

Caso o modelo produza uma classificação inválida ou esteja indisponível, regras determinísticas podem assumir o roteamento.

Em outras palavras, o modelo sugere decisões, mas o software verifica se elas são válidas.

Também foram implementados mecanismos básicos de proteção contra instruções maliciosas e exposição de informações sensíveis, além de logs estruturados e identificadores de rastreamento para acompanhar a execução.

Agora, passando aos resultados da validação.

A suíte automatizada possui setenta e quatro testes, cobrindo os agentes, as ferramentas, os contratos da API, o tratamento de erros e os cenários apresentados no desafio.

Esses testes utilizam simulações controladas para dependências externas, permitindo execuções reproduzíveis.

No meu MacBook, os setenta e quatro testes foram executados e aprovados.

Também foram realizadas duas verificações utilizando a aplicação com o modelo de linguagem local.

Na primeira, uma pergunta sobre o recebimento das vendas foi encaminhada ao Customer Support Agent, que executou ferramentas de consulta e retornou uma resposta identificada como simulada.

Na segunda, uma pergunta comparando Get Clássica e Get Smart foi direcionada ao Knowledge Agent, que produziu uma resposta sobre os produtos.

Separadamente, o relatório de validação do desenvolvimento registra dez cenários executados com o modelo real e integrações externas, todos com status de sucesso.

A documentação também registra a validação da aplicação em Docker, incluindo a construção da imagem, a execução de testes e uma chamada ao endpoint.

Essas evidências demonstram tanto a estrutura da solução quanto seu comportamento nos cenários avaliados.

Por fim, é importante reconhecer as limitações e oportunidades de evolução.

Um modelo local de três bilhões de parâmetros possui restrições de capacidade e pode produzir respostas menos precisas do que modelos maiores.

A execução local também apresenta limitações de desempenho, especialmente em equipamentos com pouca memória.

Para um ambiente de produção, eu priorizaria autenticação segura dos usuários, integração com sistemas reais de transações, monitoramento contínuo e avaliações automatizadas da qualidade das respostas.

Também implementaria métricas específicas para avaliar a precisão do roteamento, a relevância dos documentos recuperados e a fidelidade das respostas ao contexto.

A arquitetura foi estruturada de maneira modular, permitindo evoluir esses componentes sem reconstruir todo o sistema.

Em resumo, a solução demonstra como agentes especializados, recuperação de conhecimento e ferramentas podem trabalhar de forma coordenada para atender diferentes necessidades dos clientes.

O código-fonte, as especificações, as decisões arquiteturais e os relatórios de validação estão disponíveis no repositório do projeto.

Muito obrigada pela atenção.

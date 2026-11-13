# Novidade de 13/11 (manhã): abafar o jogo

- **Enquanto a voz em português fala**, o som do jogo ou do vídeo abaixa sozinho (padrão: 25%) e volta ao normal ~0,5 s depois. A voz original em inglês fica só no fundo. O volume é o do próprio programa no mixer do Windows (Chrome, RDR2.exe...), então os outros programas não mudam.
- **Começa a abafar quando a fala entra na fila**, ainda sendo gerada, então a voz em português já começa limpa.
- **Na aba Ajustes:** "Abafar o jogo" (liga/desliga) e "Volume do jogo abafado" (0 = mudo).
- **Se o programa fechar ou travar abafado**, o volume é devolvido na próxima vez que ele abrir.
- **Testado ao vivo no Red Dead:** abafou e voltou em todas as falas, sem atrasar a voz.
- **Melhor ainda, quando o jogo deixa:** zere o volume de **diálogo** nas opções de áudio do jogo (Red Dead 2 e Cyberpunk têm). Aí só a voz em inglês some e a música fica inteira.

# O que mudou na noite de 12 para 13/11

Trabalho feito sozinho, testando ao vivo com vídeos do YouTube. Os principais foram o **Red Dead Redemption 2 legendado** (bastante diálogo e nome de quem fala), o Zelda Tears of the Kingdom, o Cyberpunk e o The Last of Us.

## Resultado em números (medido ao vivo, Red Dead em tela cheia)

| | Antes (início da noite) | Agora |
|---|---|---|
| Tempo entre a legenda aparecer e a voz começar | 0,8 a 1,2 s | **~0,5 s** (mediana) |
| Picotes (silêncio no meio da fala) | 19 em 35 falas, 4,2 s | **1 em 125 falas, 0,03 s** (sessão de 5 min) |
| Memória do programa | ~6 GB | **~2,3 GB** |
| Nome de quem fala reconhecido | só "Nome:" perfeito | também com erro de OCR ("l)utch", "I.enny", "Aflhnr", "flosea", "lilly:", "A: thur:") |
| Vídeo em janela pequena | não lia | **82 falas, 0 picotes, ~0,5 s** |

Testado ao vivo em 4 jogos: Red Dead 2 (legendado), Zelda TOTK, Cyberpunk e The Last of Us 2 (dublado, sem legenda: ficou quieto, como deve). Foram 57 testes ao vivo na noite.

## Como usar com vídeo (YouTube)

1. Abra o Voz Universal.
2. Clique no nome do jogo, no alto, ou vá na aba **Caixa de diálogo**.
3. Em **"Ler de onde?"**, escolha **Tela inteira** ou a janela do navegador.
4. Dê play no vídeo. Funciona em tela cheia, com o navegador maximizado e até numa janela pequena.

O programa acha o vídeo sozinho pelo movimento da imagem e procura a legenda dentro dele. Na aba **Caixa de diálogo**, a foto mostra o retângulo do vídeo detectado e onde a última legenda foi lida.

## O que ele faz agora

- **Começa a falar mais cedo e não picota:**
  - a voz é gerada em pedaços que começam pequenos e vão crescendo;
  - antes de começar, ele espera até 120 ms pelo segundo pedaço, para não abrir um buraco de silêncio;
  - se ainda assim aparecer um buraco, ele se ajusta sozinho.
- **Legenda nova com a anterior ainda tocando:** a anterior acelera aos poucos (até 1,35x) sem mudar o tom. Só acelera quando está atrasado de verdade.
- **Não lê o que não é fala:**
  - título do vídeo e botões do YouTube;
  - créditos (nomes em maiúsculas);
  - painel de arma e menus do jogo;
  - dicas de controle ("Aperte X para...", "Abrir algibeira O");
  - aviso de tela cheia do Chrome.
- **Outro programa na frente** (WhatsApp, Discord, VS Code...): ele não lê nada até você voltar para o jogo ou vídeo.
- **Nome de quem fala:**
  - "Nome: fala", mesmo com o nome em outra cor ou lido com erro;
  - legendas empilhadas;
  - "Pessoa desconhecida 2";
  - continuação sem nome ("...do outro lado da cidade," → "a gente tava só esperando.") fica com o mesmo personagem.
- **Homem ou mulher pelo nome:** lista com ~500 nomes (`recursos/nomes.txt`, dá para acrescentar). Susan, Abigail, Karen, Tilly → mulher; Hosea, Dutch, Arthur → homem. Nomes de homem e de mulher ao mesmo tempo (Jackie, Alex, Sam) ficam de fora da lista.
- **Texto do OCR consertado:**
  - "Vocétá" → "Você tá";
  - "nào" → "não";
  - "oque" → "o que";
  - "Srta." → "Senhorita";
  - a voz não fala mais "ponto".
- **OCR piscando** (fundo claro, como neve): não repete mais a frase do começo.
- **Picotes no começo da fala:** o áudio fica sempre "ligado", para o fone ou a placa de som não cortar o começo.
- **Painel:**
  - mostra o **atraso da voz** e os **picotes** ao vivo;
  - volta do minimizado na hora (antes ficava uns segundos em branco);
  - na aba Caixa de diálogo, a foto se atualiza sozinha e mostra o vídeo detectado e onde a legenda foi lida.
- **Personagens separados por vídeo/jogo:** no modo tela inteira, cada vídeo (pelo título da janela) tem a sua lista de personagens. O "Arthur" do Red Dead não se mistura com o de outro jogo. Os que você já tinha foram para o grupo do Red Dead.
- **Correções de estabilidade:**
  - **Erro na voz clonada:** algumas frases (com "..." ou ponto seguido de minúscula) davam erro e caíam para a voz rápida. Era um bug meu; corrigido.
  - **Visão:** carrega ao abrir, antes da voz clonada, e já em meia precisão (usa menos memória). Antes ela podia carregar no meio de uma fala e abrir um buraco de 1,5 s. Carregar as duas juntas gastava 3 GB a mais, por isso uma espera a outra: a voz clonada fica pronta em ~35 s, e até lá fala a voz rápida.
  - **Registros:** os antigos são apagados sozinhos (ficam as últimas 20 sessões). Antes a pasta crescia ~100 MB por noite.
  - **Teste de 8 minutos seguidos:** a memória ficou estável (~2,3 GB), sem vazamento.
  - **Primeira fala depois de abrir:** antes levava até 4,8 s; agora a voz rápida já abre "aquecida" (~70 ms).
- **Mais acertos no nome de quem fala:**
  - **"Pessoa desconhecida 1" e "Pessoa desconhecida 2"** são pessoas diferentes e ganham vozes diferentes. Antes as duas viravam a mesma. Vale também com o começo lido errado ("V)essoa", "-4 Pessoa").
  - **Nome que aparece escrito na própria legenda** ("O Signor Bronte vai ficar...") não é mais trocado por um parecido da lista. Antes "Bronte" virava "Bonnie" e ganhava voz de mulher; "Luca" virava "Lucia".
  - **Nome com sujeira antes** ("' hornas l)ownes:" → Downes), "I'estemunha" → Testemunha, "Alngail" → Abigail.
  - **Nome lido errado no meio de uma conversa:** se a frase claramente continua a anterior, fica com quem estava falando.
  - **Testemunha, Guarda, Pessoa...** não contam mais como nome de mulher só por terminarem em "a". Nomes terminados em "y" (Tony, Jimmy, Trelawny) também não.
  - **Nome que perdeu a 1ª letra no meio da fala:** "Siga o utch." → "Siga o Dutch.". "1--losea" → "Hosea".
  - **"Arthur. Pois é."** (dois-pontos lidos como ponto) e **"l)tltchl O Hosea..."** (Dutch estragado sem os dois-pontos) agora viram o nome certo.
- **Vozes por cena:**
  - **Personagem novo** ganha a voz que **não** está sendo usada por quem falou nos últimos 3 minutos. Antes o Anthony saiu com a mesma voz do Arthur, com quem conversava.
  - **No Red Dead:** Dutch e Tilly mudaram de voz base, para não ficarem iguais ao Arthur e à Susan.
- **Criança descoberta mais tarde:** o Jack (filho do John) só chamou "mamãe" depois de 6 falas e tinha ficado com voz de homem. Agora, se as pistas novas forem fortes, o personagem muda de voz **uma vez**. Nunca muda se você fixou a voz no painel.
- **Não lê mais o que não é fala:**
  - **Barra de comandos do jogo:** "Mover para frente/trás · Girar (segurar) · Cancelar", no Zelda.
  - **Nome de item no menu:** "Arco de constructo reforçado".
  - **Painéis em maiúsculas com número ou dois-pontos:** "SISTEMA DE APOIO VITAL:", "ERRO 23", no Cyberpunk. Grito em maiúsculas ("NÃO ACREDITO NISSO!") continua sendo lido.
  - **Menu de escolhas do personagem:** linhas alinhadas à esquerda, no Cyberpunk.
  - **Dica da aba do navegador:** "(257) Cyberpunk 2077 - O Filme...".
  - **Capítulo do YouTube na barra do player:** "Final go... >".
  - **Objetivo de missão:** "Socialize com..." não vira mais nome de personagem.
  - **Troca de vídeo:** o que foi lido logo antes do título mudar é cortado.
- **Frase partida pelo OCR piscando:** "Quero dar uma boa olhada nesse" + "monstro." agora sai inteira. Uma frase nunca termina em "nesse", "de", "para"...
- **Mais consertos de OCR:** "Born" → "Bom", "Bern" → "Bem", "afundar ei" → "afundarei", "Deportaçào" → "Deportação", "distraçóes" → "distrações".

## Instalar em outra máquina

- **⚠ Use o da pasta `dist\novo\`**: é a versão final, gerada às 06:23 com tudo desta noite. O `dist\Instalar Voz Universal.exe` é de 03:04. Abri ele para conferir a tela e não consegui fechar, por isso não deu para sobrescrever.
  - Feche a janela "Instalar Voz Universal" (sem instalar).
  - Apague o `dist\Instalar Voz Universal.exe` antigo.
  - Mova o de `dist\novo\` para `dist\`.
- **O instalador:** `Instalar Voz Universal.exe` (18,8 MB). Ele instala tudo sozinho: Python, componentes, vozes, modelo da voz clonada e atalho na Área de Trabalho. Leva uns 20 minutos e baixa ~6 GB.
- **Já testado de ponta a ponta numa pasta separada nesta máquina.** No teste ele precisou tentar de novo uma vez, porque o antivírus segurou arquivos; isso já é automático.
- **Para subir no GitHub:** `.gitignore` e `README.md` estão prontos. **Deixe o repositório privado**, porque as amostras de voz vêm de jogos e da ElevenLabs. O `.exe` vai na aba *Releases*, não dentro do código.

## Ferramentas de teste (para mim, nas próximas melhorias)

| Ferramenta | O que faz |
|---|---|
| `testes\unitarios.py` | 70+ casos reais. Diz na hora se alguma mudança quebrou algo |
| `testes\ao_vivo.py` | reinicia o programa, abre um vídeo, testa em janela e em tela cheia, tira fotos |
| `testes\analisar.py` | resume uma sessão: o que leu, o que falou, atrasos, picotes, personagens |
| `testes\bateria.py` | reproduz uma sessão gravada com a voz de verdade e dá nota |

## O que ainda não está perfeito

- **Vídeo em janela pequena:** já funciona bem, mas o OCR ainda erra uma palavra ou outra ("sea gente"). Em tela cheia fica perfeito.
- **Zelda:** a fonte do jogo engana mais o OCR ("erramenta", "Íahuleta"). A caixa de texto que aparece letra por letra é falada em 2 pedaços.
- **Menu de escolhas do Cyberpunk:** quase sempre é ignorado, mas às vezes escapa uma vez enquanto o menu está aparecendo.
- **A voz clonada leva ~35 s para ficar pronta** ao abrir, porque a visão carrega antes. Até lá fala a voz rápida.
- **As duas vozes de idosa continuam provisórias:** não há amostra de uma voz de senhora de verdade. Dá para trocar no painel, na aba Banco de vozes.
- **Personagem sem nome na legenda** sai com a voz do narrador.
- **A visão** (imagem para descobrir sexo e idade) pesa pouco agora, porque em jogo ela errava a idade.

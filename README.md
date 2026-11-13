# Voz Universal

Dá voz aos diálogos e legendas de **qualquer jogo ou vídeo**, gerada na hora.

- Lê o texto da tela com o OCR do Windows: caixa de diálogo de jogo, legenda de vídeo do YouTube, tela inteira ou uma janela escolhida.
- Descobre **quem fala**: pelo nome ("Swanson: ..."), pelo jeito de falar ("obrigada", "meu jovem") e pela imagem (opcional).
- Escolhe entre **2 vozes de cada tipo** (menino, menina, homem, mulher, idoso, idosa e narrador), com variação de tom e ritmo para cada personagem. Dois personagens nunca ficam com a mesma voz.
- **Voz clonada (XTTS v2)** na placa de vídeo. Começa a falar ~0,2 s depois que a frase fica pronta.
- **Fala enquanto o texto aparece**. Quando a próxima fala chega com a anterior ainda tocando, a anterior **acelera sem mudar o tom** para alcançar.
- **Abafa o jogo enquanto fala**: abaixa o som do jogo ou do vídeo (ex.: para 25%) só enquanto a voz em português fala, e devolve depois. Liga/desliga e nível na aba *Ajustes*.
  Dica: se o jogo tiver volume de **diálogo** separado (Red Dead 2, Cyberpunk...), zere ele no jogo. Assim só a voz em inglês some e a música fica inteira.
- Painel numa janela própria: personagens, banco de vozes, região do texto, ajustes e registro.

## Instalar em outro PC

1. Baixe **`Instalar Voz Universal.exe`** (na aba *Releases* do repositório) e abra.
   Se o Windows mostrar "O Windows protegeu o computador", clique em *Mais informações* e depois em *Executar assim mesmo*. O aviso aparece porque o instalador não tem assinatura digital.
2. Clique em **Instalar**. Ele baixa o Python e os componentes (~4 GB), as vozes e o modelo da voz clonada (~1,8 GB). Leva uns 20 minutos.
3. Abra pelo atalho **Voz Universal** na Área de Trabalho.

Requisitos:
- Windows 10/11 com o pacote de **OCR em português**. Ele vem no Windows em português; se faltar, o instalador avisa.
- Placa **NVIDIA** recomendada para a voz clonada. Sem ela, a voz clonada roda no processador (comprimida em 8 bits): não picota, mas cada fala começa alguns segundos depois. Com 8 GB de RAM, feche o navegador e outros programas pesados. A voz rápida (Piper/Edge) funciona bem em qualquer PC.

## Gerar o instalador

```
python instalador\gerar_exe.py
```
O instalador sai em `dist\Instalar Voz Universal.exe`.

## Para quem desenvolve

| Arquivo | O que faz |
|---|---|
| `app.py` | início: painel + laço principal |
| `nucleo/tela.py` | acha a janela, captura, OCR, legenda de vídeo, nome de quem fala |
| `nucleo/leitor.py` | decide quando cada frase (ou pedaço) está pronta para falar |
| `nucleo/identidade.py` | sexo/idade do personagem e escolha da voz |
| `nucleo/vozes.py` | motores de voz (XTTS, Piper, Edge), fila, aceleração, alto-falante |
| `nucleo/diagnostico.py` | registro detalhado de cada etapa (`diagnostico/*.jsonl`) |
| `testes/unitarios.py` | testes rápidos com casos reais (nomes, OCR, filtros, frases). Rode depois de mexer no código |
| `testes/bateria.py` | reproduz uma sessão gravada com a voz de verdade e dá nota (atraso, buracos, pulos) |
| `testes/analisar.py` | resumo legível de uma sessão (`diagnostico/*.jsonl`) |
| `testes/ao_vivo.py` | teste automático com um vídeo do YouTube (janela normal + tela cheia) |
| `testes/fechar_todos.py` | fecha qualquer Voz Universal aberto (antes dos testes) |
| `recursos/nomes.txt` | nomes próprios e o sexo (ajuda a escolher a voz); dá para acrescentar |

Cada sessão grava em `diagnostico/` um registro detalhado (o que foi lido, cada pedaço de voz gerado, buracos, acelerações) e recortes das legendas em `diagnostico/quadros/`.

As amostras em `banco_vozes/` servem para clonar as vozes. **Deixe o repositório privado**: algumas amostras vêm de dublagem de jogo e de vozes da ElevenLabs.

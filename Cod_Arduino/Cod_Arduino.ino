#include <SPI.h>
#include <MFRC522.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ============================================================
//  CONFIGURAÇÃO DE PINOS
// ============================================================
#define SS_PIN        5
#define RST_PIN       22
#define RELAY_PIN     4
#define LED_VERDE     26
#define LED_VERMELHO  27
#define BUZZER_PIN    25   // buzzer passivo: + no GPIO 25, - no GND
#define LED_AZUL      32   // aceso fixo = Wi-Fi E internet funcionando; azul e amarelo piscando alternados = Wi-Fi conectado mas SEM internet
#define LED_AMARELO   33   // piscando rápido = Wi-Fi desconectado

// ============================================================
//  CONFIGURAÇÕES — ALTERE AQUI
// ============================================================
const char* WIFI_SSID  = "nome do wifi";
const char* WIFI_PASS  = "senha do wifi";

// IP do computador/servidor (no Windows: ipconfig -> IPv4)
const char* SERVER_URL = "http://IP_DO_SERVIDOR:5000/api/acesso";

// Copie o conteúdo do arquivo api.key gerado pelo servidor
const char* API_KEY    = "api_key";

// ID deste laboratório (porta), igual ao da coluna "ID" na tela "Laboratórios" do site.
// Pode ter letras e/ou números (ex.: "1", "MAKER", "INFO-101"), sempre entre aspas.
// Cada ESP32 usa o mesmo código, mudando só esta linha.
const char* LAB_ID = "1";

const bool RELE_ATIVO_ALTO = false;              // relé ativo em LOW: LOW liga, HIGH desliga (repouso = HIGH)
const unsigned long MEIO_PERIODO_MS    = 50;     // 10 Hz -> período de 100 ms (50 ms ligado + 50 ms desligado)
const int            CICLOS_FECHADURA  = 10;     // 10 ciclos x 100 ms = 1000 ms de acionamento
const unsigned long DURACAO_SOM_APROVADO = 500;  // ms do bip de aprovação (toca junto com o relé)
const unsigned long TEMPO_LED          = 2000;   // ms
const unsigned long INTERVALO_WIFI     = 15000;  // ms entre tentativas de reconexão
const unsigned long PISCA_AMARELO      = 150;    // ms aceso / apagado do LED amarelo (pisca rápido)
const unsigned long INTERVALO_LEITOR   = 3000;   // ms entre checagens de saúde do leitor RC522

// ---- teste de internet ----
const unsigned long PISCA_ALTERNADO_SEM_NET = 500;   // ms de cada cor (azul/amarelo alternados) quando há Wi-Fi mas não há internet
const unsigned long INTERVALO_NET_OK       = 15000;  // ms entre testes quando a internet está funcionando
const unsigned long INTERVALO_NET_FALHA    = 5000;   // ms entre testes quando está sem internet (volta a azul fixo mais rápido)
const int32_t       TIMEOUT_NET            = 1500;   // ms que cada teste espera por resposta

MFRC522 mfrc522(SS_PIN, RST_PIN);

unsigned long apagaLedsEm  = 0;
unsigned long fimSomEm     = 0;   // quando o bip de aprovação deve parar (0 = sem bip tocando)
unsigned long ultimaTentativaWiFi = 0;
unsigned long ultimoPisca = 0;
bool amareloLigado = false;
unsigned long ultimaChecagemLeitor = 0;
bool wifiEstavaConectado = false;
volatile bool internetOk = false;     // escrito pela tarefa de teste de internet, lido pelo loop
unsigned long ultimoPiscaAzul = 0;
bool azulLigado = false;

// ---------- SONS ----------
// Som de aprovação (Bip único e limpo - Total: 500 ms / 0,5s)
void somAprovado() {
  tone(BUZZER_PIN, 1318, 500);   // Tom agudo e agradável (Mi 6 - 1318 Hz)
  delay(500);                    // Mantém a execução por exatos 0,5 segundos
  noTone(BUZZER_PIN);
}

// Som de reprovação (Total: 1s)
void somReprovado() {
  tone(BUZZER_PIN, 180, 400);    // Tom grave
  delay(450);
  tone(BUZZER_PIN, 130, 500);    // Tom mais grave
  delay(550);
  noTone(BUZZER_PIN);
}

// ---------- AUXILIARES ----------
void setRele(bool ativo) {
  digitalWrite(RELAY_PIN, (ativo == RELE_ATIVO_ALTO) ? HIGH : LOW);
}

// Espera "ms" milissegundos e, enquanto espera, desliga o buzzer quando acabar o tempo do bip.
void espera(unsigned long ms) {
  unsigned long inicio = millis();
  while (millis() - inicio < ms) {
    if (fimSomEm && millis() >= fimSomEm) {      // acabou o tempo do bip: desliga o buzzer
      noTone(BUZZER_PIN);
      fimSomEm = 0;
    }
  }
}

// Liga e desliga o relé a 10 Hz por 1000 ms e deixa o pino em repouso (HIGH).
void abrirFechadura() {
  for (int i = 0; i < CICLOS_FECHADURA; i++) {
    setRele(true);                               // relé liga (LOW)
    espera(MEIO_PERIODO_MS);
    setRele(false);                              // relé desliga (HIGH)
    espera(MEIO_PERIODO_MS);
  }
  setRele(false);                                // depois de 1 s o pino volta a HIGH
}

// Inicia UMA tentativa de conexão (não trava o programa).
// Cancela a tentativa anterior antes de começar outra, para evitar
// o erro "sta is connecting, cannot set config".
void iniciaConexaoWiFi() {
  ultimaTentativaWiFi = millis();
  Serial.println("Conectando ao Wi-Fi...");
  WiFi.disconnect(true);
  delay(200);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

// Chamada no loop: só tenta de novo se caiu e já passou o intervalo.
void verificaWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  if (millis() - ultimaTentativaWiFi < INTERVALO_WIFI) return;
  iniciaConexaoWiFi();
}

// Diagnóstico: lista as redes que o ESP32 enxerga.
void listaRedes() {
  Serial.println("--- Redes encontradas (o ESP32 só enxerga 2,4 GHz) ---");
  int n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    Serial.printf("%s | sinal %d dBm | canal %d | seguranca %d\n",
                  WiFi.SSID(i).c_str(), WiFi.RSSI(i), WiFi.channel(i),
                  (int)WiFi.encryptionType(i));
  }
  if (n <= 0) Serial.println("Nenhuma rede encontrada.");
  Serial.println("(seguranca 5 = WPA2-Enterprise, nao funciona com senha simples)");
  Serial.println("------------------------------------------------------");
  WiFi.scanDelete();
}

String lerUID() {
  String uid = "";
  for (byte i = 0; i < mfrc522.uid.size; i++) {
    if (mfrc522.uid.uidByte[i] < 0x10) uid += "0";
    uid += String(mfrc522.uid.uidByte[i], HEX);
    if (i < mfrc522.uid.size - 1) uid += " ";
  }
  uid.toUpperCase();
  return uid;
}

// Retorna: 1 = autorizado, 0 = negado, -1 = erro de comunicação
int consultaServidor(const String& uid, String& nome) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Sem Wi-Fi, não consulta o servidor.");
    return -1;
  }

  WiFiClient client;          // HTTP simples (não Secure)
  HTTPClient http;
  http.setTimeout(4000);

  Serial.print("URL: ");
  Serial.println(SERVER_URL);
  if (!http.begin(client, SERVER_URL)) {
    Serial.println("Falha no http.begin()");
    return -1;
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-Key", API_KEY);

  JsonDocument req;
  req["uid"] = uid;
  req["lab"] = LAB_ID;
  String corpo;
  serializeJson(req, corpo);

  Serial.print("POST: ");
  Serial.println(corpo);

  int codigo = http.POST(corpo);
  if (codigo != 200) {
    Serial.printf("Erro HTTP: %d\n", codigo);
    if (codigo > 0) Serial.println(http.getString());
    http.end();
    return -1;
  }

  String resposta = http.getString();
  resposta.trim();            // tira o "\n" que o servidor manda no fim (evita linha em branco no serial)
  http.end();
  resposta.trim();                 // tira o \n que o servidor manda no fim
  // mostra o JSON com uma chave por linha (só para leitura no monitor serial)
  String bonito = resposta;
  bonito.trim();
  bonito.replace("{", "{\n");
  bonito.replace(",", ",\n");
  bonito.replace("}", "\n}");
  Serial.print("Resposta: ");
  Serial.println(bonito);

  JsonDocument resp;
  DeserializationError erro = deserializeJson(resp, resposta);
  if (erro) {
    Serial.print("Erro JSON: ");
    Serial.println(erro.c_str());
    return -1;
  }

  nome = resp["nome"] | "";
  return resp["autorizado"] ? 1 : 0;
}

void mostraResultado(bool liberado) {
  digitalWrite(LED_VERDE, liberado ? HIGH : LOW);
  digitalWrite(LED_VERMELHO, liberado ? LOW : HIGH);
  apagaLedsEm = millis() + TEMPO_LED;

  if (liberado) {
    // LED verde (já aceso acima), buzzer e relé começam ao mesmo tempo
    tone(BUZZER_PIN, 1318);                     // buzzer liga (Mi 6) e o código segue sem esperar
    fimSomEm = millis() + DURACAO_SOM_APROVADO; // o bip é desligado dentro de espera()
    abrirFechadura();                           // relé a 10 Hz por 1000 ms, com o bip tocando junto
    noTone(BUZZER_PIN);                         // garante o buzzer desligado
    fimSomEm = 0;
    Serial.println("Fechadura: acionamento concluido (1000 ms).");
  } else {
    somReprovado();                             // som de recusado (1 s)
  }
}

// Avisa no monitor serial quando o Wi-Fi conecta ou cai
// (antes só aparecia "Conectando..." e parecia travado mesmo depois de reconectar).
void eventoWiFi(WiFiEvent_t evento, WiFiEventInfo_t info) {
  if (evento == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    wifiEstavaConectado = true;
    Serial.print("Wi-Fi conectado. IP: ");
    Serial.println(WiFi.localIP());
  } else if (evento == ARDUINO_EVENT_WIFI_STA_DISCONNECTED && wifiEstavaConectado) {
    wifiEstavaConectado = false;
    Serial.printf("Wi-Fi desconectado (motivo %d). Tentando reconectar...\n",
                  info.wifi_sta_disconnected.reason);
  }
}

// O RC522 pode "resetar" sozinho (queda de tensão quando o Wi-Fi religa) e ficar
// com a antena desligada, sem o ESP32 perceber. Checa de tempos em tempos e reinicia.
void verificaLeitor() {
  if (millis() - ultimaChecagemLeitor < INTERVALO_LEITOR) return;
  ultimaChecagemLeitor = millis();
  byte versao = mfrc522.PCD_ReadRegister(MFRC522::VersionReg);
  byte antena = mfrc522.PCD_ReadRegister(MFRC522::TxControlReg);
  if (versao == 0x00 || versao == 0xFF || (antena & 0x03) != 0x03) {
    Serial.println("Leitor RC522 sem resposta ou com a antena desligada. Reiniciando o leitor...");
    mfrc522.PCD_Init();
  }
}

// Testa se existe internet de verdade: o Wi-Fi conectado só prova que o ESP32 chegou no roteador.
// Aqui abrimos uma conexão TCP (porta 443) com servidores públicos (Cloudflare e Google); se um deles
// atender, há internet. Se a rede bloquear esses endereços, troque os IPs abaixo.
bool testaInternet() {
  const IPAddress alvos[] = { IPAddress(1, 1, 1, 1), IPAddress(8, 8, 8, 8) };
  for (int i = 0; i < 2; i++) {
    WiFiClient teste;
    bool ok = teste.connect(alvos[i], 443, TIMEOUT_NET);
    teste.stop();
    if (ok) return true;
  }
  return false;
}

// Roda em paralelo (outra tarefa do FreeRTOS), então a espera do teste não atrasa a leitura do crachá.
void tarefaInternet(void* parametro) {
  bool anterior = false;
  for (;;) {
    bool conectado = (WiFi.status() == WL_CONNECTED);
    bool ok = conectado ? testaInternet() : false;
    internetOk = ok;
    if (conectado && ok != anterior) {
      Serial.println(ok ? "Internet OK." : "Wi-Fi conectado, mas SEM acesso a internet.");
    }
    if (conectado) anterior = ok;
    unsigned long espera = !conectado ? 1000 : (ok ? INTERVALO_NET_OK : INTERVALO_NET_FALHA);
    vTaskDelay(pdMS_TO_TICKS(espera));
  }
}

// LED de status (não trava o programa):
//   sem Wi-Fi                  -> azul apagado, amarelo piscando rápido
//   Wi-Fi sem internet         -> azul e amarelo piscando alternados (um aceso enquanto o outro está apagado)
//   Wi-Fi com internet         -> amarelo apagado, azul aceso fixo
void atualizaLedWiFi() {
  unsigned long agora = millis();

  if (WiFi.status() == WL_CONNECTED) {
    if (internetOk) {
      digitalWrite(LED_AZUL, HIGH);
      digitalWrite(LED_AMARELO, LOW);
      azulLigado = true;
      amareloLigado = false;
    } else if (agora - ultimoPiscaAzul >= PISCA_ALTERNADO_SEM_NET) {
      ultimoPiscaAzul = agora;
      azulLigado = !azulLigado;
      amareloLigado = !azulLigado;
      digitalWrite(LED_AZUL, azulLigado ? HIGH : LOW);
      digitalWrite(LED_AMARELO, amareloLigado ? HIGH : LOW);
    }
    return;
  }

  digitalWrite(LED_AZUL, LOW);
  azulLigado = false;
  if (agora - ultimoPisca >= PISCA_AMARELO) {
    ultimoPisca = agora;
    amareloLigado = !amareloLigado;
    digitalWrite(LED_AMARELO, amareloLigado ? HIGH : LOW);
  }
}

// ---------- SETUP ----------
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("=== Sistema de acesso ===");
  WiFi.onEvent(eventoWiFi);

  pinMode(RELAY_PIN, OUTPUT);
  pinMode(LED_VERDE, OUTPUT);
  pinMode(LED_VERMELHO, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LED_AZUL, OUTPUT);
  pinMode(LED_AMARELO, OUTPUT);
  digitalWrite(LED_AZUL, LOW);
  digitalWrite(LED_AMARELO, LOW);
  setRele(false);
  digitalWrite(LED_VERDE, LOW);
  digitalWrite(LED_VERMELHO, LOW);

  SPI.begin();                  // SPI padrão: SCK=18, MISO=19, MOSI=23, SS=5
  mfrc522.PCD_Init();
  delay(50);
  byte v = mfrc522.PCD_ReadRegister(MFRC522::VersionReg);
  Serial.print("Versao RC522: 0x");
  Serial.println(v, HEX);
  if (v == 0x00 || v == 0xFF) {
    Serial.println("!! RC522 nao respondeu. Verifique fiacao/3.3V.");
  }

  Serial.print("MAC do ESP32: ");
  Serial.println(WiFi.macAddress());

  // Primeira conexão: espera até 15 s
  iniciaConexaoWiFi();
  unsigned long ini = millis();
  unsigned long ultimoPonto = 0;
  while (WiFi.status() != WL_CONNECTED && millis() - ini < 15000) {
    atualizaLedWiFi();                       // amarelo piscando enquanto conecta
    if (millis() - ultimoPonto >= 300) {
      ultimoPonto = millis();
      Serial.print(".");
    }
    delay(10);
  }
  atualizaLedWiFi();
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Wi-Fi OK  IP: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Wi-Fi falhou. Veja a lista de redes abaixo:");
    WiFi.disconnect(true);
    delay(200);
    digitalWrite(LED_AMARELO, HIGH);          // a varredura trava o pisca por alguns segundos
    listaRedes();
    ultimaTentativaWiFi = millis();   // o loop tenta de novo depois
  }

  // Teste de internet em segundo plano (núcleo 0, para não atrapalhar o loop do leitor)
  xTaskCreatePinnedToCore(tarefaInternet, "internet", 4096, NULL, 1, NULL, 0);

  Serial.println("Aproxime o cartão...");
}

// ---------- LOOP ----------
void loop() {
  unsigned long agora = millis();

  if (apagaLedsEm && agora >= apagaLedsEm) {
    digitalWrite(LED_VERDE, LOW);
    digitalWrite(LED_VERMELHO, LOW);
    apagaLedsEm = 0;
  }

  verificaWiFi();
  atualizaLedWiFi();
  verificaLeitor();

  if (!mfrc522.PICC_IsNewCardPresent()) return;
  if (!mfrc522.PICC_ReadCardSerial())   return;

  String uid = lerUID();
  Serial.println("UID lido: " + uid);

  String nome;
  int resultado = consultaServidor(uid, nome);

  if (resultado == 1) {
    Serial.println("Acesso liberado: " + nome);
    mostraResultado(true);
  } else if (resultado == 0) {
    Serial.println("Acesso negado.");
    mostraResultado(false);
  } else {
    Serial.println("Erro de comunicação. Porta trancada.");
    mostraResultado(false);
  }
  Serial.println();                // linha em branco separando cada leitura

  mfrc522.PICC_HaltA();
  mfrc522.PCD_StopCrypto1();
}

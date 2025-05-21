# JBGAnnualReportAnalyzer

En FastAPI-baserad webbapplikation för att analysera och extrahera nyckeltal från årsredovisningar för arbetslöshetskassor.

## 🧰 Funktioner

* Ladda upp och bearbeta årsredovisningar i PDF-format (eller ZIP-filer med flera PDF:er).
* Maskera känsliga uppgifter i PDF-filer
* Extrahera och analysera viktiga finansiella nyckeltal.
* Användarvänligt HTML-gränssnitt för smidig interaktion.
* Dockeriserad uppsättning för enkel distribution.

## 📁 Projektstruktur

```bash
JBGAnnualReportAnalyzer/
├── app/
│   ├── main.py
│   ├── templates/
│   └── static/
├── requirements.txt
├── Dockerfile
└── README.md
```



## ⚙️ Installation

### Förutsättningar

* Python 3.8 eller högre
* Git
* Docker (för containerbaserad körning)

### Klona repot

```bash
git clone https://github.com/slimebob1975/JBGAnnualReportAnalyzer.git
cd JBGAnnualReportAnalyzer
```



### Installera beroenden

```bash
pip install -r requirements.txt
```



## ▶️ Körning

### Lokalt med Uvicorn

```bash
uvicorn app.main:app --reload
```



Öppna sedan din webbläsare och navigera till: [http://127.0.0.1:8000](http://127.0.0.1:8000)

### Med Docker

```bash
docker build -t jbg-analyzer .
docker run -d -p 8000:8000 jbg-analyzer
```



## 📝 Användning

1. Navigera till webbapplikationen i din webbläsare.
2. Ladda upp en årsredovisning i PDF-format eller en ZIP-fil med flera PDF:er.
3. Applikationen kommer att bearbeta filerna och extrahera relevanta nyckeltal.
4. Resultaten presenteras i ett användarvänligt gränssnitt.

## 📄 Licens

Detta projekt är licensierat under GPL-3.0. Se [LICENSE](LICENSE) för mer information.

---

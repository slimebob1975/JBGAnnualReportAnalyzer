# 📊 JBGAnnualReportAnalyzer

A FastAPI-based web application designed to analyze and extract key financial metrics from annual reports of unemployment insurance organizations.

## 🚀 Features

- Upload and process annual reports in PDF (or ZIP of PDF) format.
- Extract and analyze key financial indicators.
- User-friendly HTML interface for seamless interactions.
- Dockerized setup for easy deployment.

## 📂 Project Structure

```
JBGAnnualReportAnalyzer/
├── app/
│   ├── main.py
│   ├── templates/
│   └── static/
├── requirements.txt
├── Dockerfile
└── README.md
```

## 🛠️ Installation

### Prerequisites

- Python 3.8 or higher
- Git
- Docker (optional, for containerized deployment)

### Steps

1. **Clone the Repository:**

   ```bash
   git clone https://github.com/slimebob1975/JBGAnnualReportAnalyzer.git
   cd JBGAnnualReportAnalyzer
   ```

2. **Set Up Virtual Environment:**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

4. **Run the Application:**

   ```bash
   uvicorn app.main:app --reload
   ```

   Access the application at [http://127.0.0.1:8000](http://127.0.0.1:8000)

## 🐳 Docker Deployment

1. **Build the Docker Image:**

   ```bash
   docker build -t jbg-analyzer .
   ```

2. **Run the Docker Container:**

   ```bash
   docker run -d -p 8000:8000 jbg-analyzer
   ```

   The application will be accessible at [http://localhost:8000](http://localhost:8000)

## 📄 License

This project is licensed under the [GPL-3.0 License](LICENSE).

## 🤝 Contributing

Contributions are welcome! Please fork the repository and submit a pull request for any enhancements or bug fixes.

## 📬 Contact

For any inquiries or support, please open an issue on the [GitHub repository](https://github.com/slimebob1975/JBGAnnualReportAnalyzer/issues).

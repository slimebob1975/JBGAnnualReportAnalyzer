document.addEventListener("DOMContentLoaded", function () {
    const savedKey = localStorage.getItem("openai_api_key");
    if (savedKey) {
        const apikeyField = document.getElementById("apikey");
        if (apikeyField) apikeyField.value = savedKey;
    }

    const form = document.querySelector("form");
    const spinner = document.getElementById("spinner-container");

    if (form && spinner) {
        form.addEventListener("submit", function () {
            const apikeyField = document.getElementById("apikey");
            if (apikeyField) {
                const apikey = apikeyField.value.trim();
                localStorage.setItem("openai_api_key", apikey);
            }
            spinner.style.display = "block";
        });
    }
});
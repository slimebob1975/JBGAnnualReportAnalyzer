document.addEventListener("DOMContentLoaded", function () {
    const savedKey = localStorage.getItem("openai_api_key");
    if (savedKey) {
        const apikeyField = document.getElementById("apikey");
        if (apikeyField) apikeyField.value = savedKey;
    }

    const spinner = document.getElementById("spinner-container");

    // Hantera alla formulär på sidan
    document.querySelectorAll("form").forEach(form => {
        form.addEventListener("submit", function () {
            // Spara API-nyckel om det finns
            const apikeyField = form.querySelector("#apikey");
            if (apikeyField) {
                const apikey = apikeyField.value.trim();
                localStorage.setItem("openai_api_key", apikey);
            }

            // Visa spinner
            if (spinner) {
                spinner.classList.add("active");                
                console.log("Spinner activated");
            }
        });
    });
});

function showTab(tabId) {
    console.log("showTab called with tabId:", tabId);

    // Göm alla flikar
    document.querySelectorAll(".tab-content").forEach(tab => {
        console.log("Hiding tab:", tab.id);
        tab.classList.remove("active");
    });

    // Visa vald flik
    const selectedTab = document.getElementById(tabId);
    if (selectedTab) {
        console.log("Activating tab:", selectedTab.id);
        selectedTab.classList.add("active");
    } else {
        console.warn("Tab not found:", tabId);
    }
}

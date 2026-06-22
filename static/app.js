document.addEventListener("submit", function (event) {
    var message = event.target.getAttribute("data-confirm");

    if (message && !window.confirm(message)) {
        event.preventDefault();
    }
});

function detectStore(url) {
    var lower = (url || "").toLowerCase();
    if (lower.indexOf("amazon.") !== -1) return "Amazon";
    if (lower.indexOf("bestbuy.") !== -1) return "Best Buy";
    if (lower.indexOf("target.") !== -1) return "Target";
    if (lower.indexOf("walmart.") !== -1) return "Walmart";
    if (lower.indexOf("homedepot.") !== -1) return "Home Depot";
    if (lower.indexOf("newegg.") !== -1) return "Newegg";
    return "Other website";
}

document.addEventListener("input", function (event) {
    if (event.target.name !== "url") return;

    var store = detectStore(event.target.value);
    var label = document.getElementById("detected-store");
    var allow = document.getElementById("allow-crawlbase");
    var prefer = document.getElementById("prefer-crawlbase");

    if (label) label.textContent = store;
    if (!allow || !prefer) return;

    if (store === "Best Buy") {
        allow.checked = true;
        prefer.checked = true;
    } else if (["Target", "Walmart", "Home Depot", "Newegg"].indexOf(store) !== -1) {
        allow.checked = true;
    }
});

function refreshSavedStockQuotes() {
    var stockCards = document.querySelectorAll(".stock-card[data-stock-id]");
    if (!stockCards.length || !window.fetch) return;

    fetch("/stocks/latest.json", { credentials: "same-origin" })
        .then(function (response) {
            if (!response.ok) throw new Error("Could not refresh stock cards.");
            return response.json();
        })
        .then(function (data) {
            (data.stocks || []).forEach(function (stock) {
                var card = document.querySelector('.stock-card[data-stock-id="' + stock.id + '"]');
                if (!card) return;

                var price = card.querySelector("[data-stock-price]");
                var checked = card.querySelector("[data-stock-checked]");
                var status = card.querySelector("[data-stock-status]");
                var change = card.querySelector("[data-stock-change]");

                if (price) price.textContent = stock.latest_price;
                if (checked) checked.textContent = stock.last_checked_at;
                if (change) change.textContent = stock.percent_change;
                if (status) {
                    status.textContent = stock.status;
                    status.className = "status " + stock.status_class;
                }
            });
        })
        .catch(function () {
            // Quietly keep showing the latest saved values already on the page.
        });
}

window.setInterval(refreshSavedStockQuotes, 60000);

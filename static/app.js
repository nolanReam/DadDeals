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

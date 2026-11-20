// Finish switch for the concept render. No server calls.
(function () {
  "use strict";
  var stage = document.getElementById("pf-stage");
  var buttons = document.querySelectorAll(".pf-sw");
  if (!stage) return;
  buttons.forEach(function (b) {
    b.addEventListener("click", function () {
      stage.dataset.concept = b.dataset.concept;
      buttons.forEach(function (x) { x.setAttribute("aria-pressed", String(x === b)); });
    });
  });
  var reel = document.getElementById("pf-reel");
  document.querySelectorAll(".pf-arrow").forEach(function (b) {
    b.addEventListener("click", function () {
      var card = reel && reel.querySelector(".reel");
      if (card) reel.scrollBy({ left: Number(b.dataset.dir) * (card.offsetWidth + 16), behavior: "smooth" });
    });
  });
})();

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
})();

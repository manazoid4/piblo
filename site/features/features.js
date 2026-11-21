// Finish switch for the concept render. No server calls.
(function () {
  "use strict";
  var stage = document.getElementById("pf-stage");
  var buttons = document.querySelectorAll(".pf-sw");
  var image = stage && stage.querySelector("img");
  if (!image) return;
  var finishes = {
    cream: { src: image.getAttribute("src"), alt: image.alt },
    smoke: {
      src: "/media/concept/piblo-concept-smoke.jpg",
      alt: "Piblo concept in a smoked clear finish, standing on its kickstand."
    }
  };
  buttons.forEach(function (b) {
    b.addEventListener("click", function () {
      var finish = finishes[b.dataset.concept];
      image.src = finish.src;
      image.alt = finish.alt;
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

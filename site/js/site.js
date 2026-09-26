(function () {
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.querySelector("#site-nav");
  if (!toggle || !nav) return;

  function setOpen(open) {
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.textContent = open ? "Close" : "Menu";
    nav.classList.toggle("is-open", open);
    document.body.classList.toggle("nav-open", open);
  }

  toggle.addEventListener("click", function () {
    setOpen(toggle.getAttribute("aria-expanded") !== "true");
  });

  nav.addEventListener("click", function (event) {
    if (event.target.closest("a")) setOpen(false);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setOpen(false);
  });
})();

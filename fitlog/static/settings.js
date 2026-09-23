document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-check]");
  if (!button) return;
  const checked = button.dataset.check === "all";
  for (const box of button.closest("fieldset").querySelectorAll("input[type=checkbox]")) {
    box.checked = checked;
  }
});

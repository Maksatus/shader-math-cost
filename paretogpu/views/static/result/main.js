new MutationObserver(() => autoTips(document.body)).observe(document.body, {childList: true, subtree: true});
autoTips(document.body);
render();

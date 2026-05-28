(function(){
    var apply = function() {
        try {
            var html = document.documentElement;
            if (html) {
                html.style.transformOrigin = 'top left';
                html.style.transform = 'scale(0.5)';
                html.style.width = '200%';
                html.style.height = '200%';
            }
        } catch(e) {}
    };
    try { apply(); } catch(e) {}
    document.addEventListener('DOMContentLoaded', apply, true);
    window.addEventListener('load', apply, true);
})();

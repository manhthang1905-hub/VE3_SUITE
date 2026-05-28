(function(){
    var _applyZoom=function(){try{document.documentElement.style.zoom='50%';}catch(e){}if(document.body)try{document.body.style.zoom='100%';}catch(e){}};
    try{_applyZoom();}catch(e){}
    document.addEventListener('DOMContentLoaded',_applyZoom,true);
    window.addEventListener('load',_applyZoom,true);
})();


    (function(){
        // WebGL spoof
        var V="Google Inc. (Intel)",R="ANGLE (Intel, Intel(R) HD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)";
        var gp=WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter=function(p){
            if(p===37445||p===0x9245)return V;
            if(p===37446||p===0x9246)return R;
            return gp.call(this,p);
        };
        if(typeof WebGL2RenderingContext!=='undefined'){
            var gp2=WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter=function(p){
                if(p===37445||p===0x9245)return V;
                if(p===37446||p===0x9246)return R;
                return gp2.call(this,p);
            };
        }
        // Canvas noise
        var otd=HTMLCanvasElement.prototype.toDataURL;
        HTMLCanvasElement.prototype.toDataURL=function(t){
            try{var c=this.getContext('2d');if(c){var d=c.getImageData(0,0,Math.min(this.width,2),1);
            if(d.data.length>=4){d.data[0]=(d.data[0]+1)%256;d.data[1]=(d.data[1]+5)%256;d.data[2]=(d.data[2]+8)%256;c.putImageData(d,0,0);}}}catch(e){}
            return otd.call(this,t);
        };
        // Hardware
        try{Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>4,configurable:true});}catch(e){}
        try{Object.defineProperty(navigator,'deviceMemory',{get:()=>32,configurable:true});}catch(e){}
        // Screen
        try{Object.defineProperty(screen,'width',{get:()=>1280,configurable:true});}catch(e){}
        try{Object.defineProperty(screen,'height',{get:()=>720,configurable:true});}catch(e){}
        try{Object.defineProperty(screen,'availWidth',{get:()=>1280,configurable:true});}catch(e){}
        try{Object.defineProperty(screen,'availHeight',{get:()=>720-40,configurable:true});}catch(e){}
        // Audio
        var ogf=AnalyserNode.prototype.getFloatFrequencyData;
        AnalyserNode.prototype.getFloatFrequencyData=function(a){ogf.call(this,a);for(var i=0;i<Math.min(a.length,10);i++)a[i]+=-0.142294;};
        console.log('[SPOOF] seed=8012748 gpu=ANGLE (Intel, Intel(R) HD Grap...');
        // Page zoom
        (function(){
            var z='50%';
            function applyZoom(){ try{ document.documentElement.style.zoom=z; }catch(e){} }
            if(document.readyState==='loading'){ document.addEventListener('DOMContentLoaded',applyZoom); }
            else{ applyZoom(); }
            new MutationObserver(function(){ if(document.documentElement && document.documentElement.style.zoom!==z) applyZoom(); }).observe(document.documentElement||document,{childList:true,subtree:false});
        })();
    })();
    
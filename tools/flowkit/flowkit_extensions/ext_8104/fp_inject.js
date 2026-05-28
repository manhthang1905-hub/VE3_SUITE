
    (function(){
        // WebGL spoof
        var V="Google Inc. (NVIDIA)",R="ANGLE (NVIDIA, NVIDIA GeForce RTX 2080 Direct3D11 vs_5_0 ps_5_0, D3D11)";
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
            if(d.data.length>=4){d.data[0]=(d.data[0]+4)%256;d.data[1]=(d.data[1]+7)%256;d.data[2]=(d.data[2]+3)%256;c.putImageData(d,0,0);}}}catch(e){}
            return otd.call(this,t);
        };
        // Hardware
        try{Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>20,configurable:true});}catch(e){}
        try{Object.defineProperty(navigator,'deviceMemory',{get:()=>16,configurable:true});}catch(e){}
        // Screen
        try{Object.defineProperty(screen,'width',{get:()=>1280,configurable:true});}catch(e){}
        try{Object.defineProperty(screen,'height',{get:()=>720,configurable:true});}catch(e){}
        try{Object.defineProperty(screen,'availWidth',{get:()=>1280,configurable:true});}catch(e){}
        try{Object.defineProperty(screen,'availHeight',{get:()=>720-40,configurable:true});}catch(e){}
        // Audio
        var ogf=AnalyserNode.prototype.getFloatFrequencyData;
        AnalyserNode.prototype.getFloatFrequencyData=function(a){ogf.call(this,a);for(var i=0;i<Math.min(a.length,10);i++)a[i]+=-0.070909;};
        console.log('[SPOOF] seed=7474108 gpu=ANGLE (NVIDIA, NVIDIA GeForce ...');
    })();
    
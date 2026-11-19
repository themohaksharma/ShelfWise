/* Shared sign-in and shop registration form behavior. */
(()=>{'use strict';
const byId=id=>document.getElementById(id);
function message(error,text){error.textContent=text||''}
const login=byId('loginForm');
if(login)login.addEventListener('submit',async event=>{event.preventDefault();const error=byId('error');message(error,'');try{await ShelfWiseAuth.login(byId('identifier').value,byId('password').value);const params=new URLSearchParams(location.search),requested=params.get('next')||'/app';location.href=requested.startsWith('/')&&!requested.startsWith('//')?requested:'/app'}catch(err){message(error,err.message)}});
const register=byId('registerForm');
if(register)register.addEventListener('submit',async event=>{event.preventDefault();const error=byId('error');message(error,'');try{await ShelfWiseAuth.register({owner:byId('owner').value,shopName:byId('shopName').value,email:byId('identifier').value,category:byId('category').value,address:byId('address').value,password:byId('password').value});location.href='/app'}catch(err){message(error,err.message)}});
})();

FROM nginx:alpine

# Copy custom Nginx configuration with UTF-8 charset
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy all static novel web pages and assets
COPY . /usr/share/nginx/html

EXPOSE 80 21041

CMD ["nginx", "-g", "daemon off;"]

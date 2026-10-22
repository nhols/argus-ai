FROM node:24-alpine

RUN apk add --no-cache ffmpeg

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --production

COPY . .

EXPOSE 8080
CMD ["node", "index.js"]

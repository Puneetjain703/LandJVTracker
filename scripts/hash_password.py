import getpass

from passlib.context import CryptContext


if __name__ == "__main__":
    password = getpass.getpass("Password to hash: ")
    print(CryptContext(schemes=["bcrypt"], deprecated="auto").hash(password))

